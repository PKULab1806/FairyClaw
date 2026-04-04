# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Memory extraction hook implementing Hybrid Memory Storage Phase."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from fairyclaw.capabilities.memory_hooks.scripts._vectorstore import LocalVectorStore
from fairyclaw.config.loader import load_yaml
from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, ToolCallRound
from fairyclaw.core.agent.hooks.protocol import AfterLlmResponseHookPayload, HookStageInput, HookStageOutput, HookStatus
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.infrastructure.embedding.service import create_embedding_service
from fairyclaw.infrastructure.llm.factory import create_llm_client

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

DEFAULT_CONFIG = {
    "extraction_mode": "hybrid",
    "extraction_profile": "compaction_summarizer",
    "compaction_profile": "compaction_summarizer",
    "embedding_profile": "embedding",
    "vectorstore_path": "./data/vectorstore",
    "collection_name": "fairyclaw_memory",
}

def _load_config() -> dict[str, object]:
    raw = load_yaml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    return config


def _extraction_llm_profile(config: dict[str, object]) -> str:
    """Profile name from llm_endpoints.yaml; extraction_profile overrides compaction_profile."""
    name = str(config.get("extraction_profile") or config.get("compaction_profile") or "").strip()
    return name or "compaction_summarizer"


def _preview(text: str, limit: int = 200) -> str:
    one = " ".join(str(text or "").split())
    if len(one) <= limit:
        return one
    return f"{one[: limit - 3]}..."


def _build_recent_transcript(history: list[Any], payload: AfterLlmResponseHookPayload) -> str:
    """Build a transcript from recent history items and the current LLM response."""
    lines = []
    
    # 1. Grab context from the last few events (messages + tool results)
    for item in history[-6:]:  
        if isinstance(item, SessionMessageBlock):
            text = item.as_plain_text().strip()
            if text:
                lines.append(f"{item.role.value.capitalize()}: {text}")
        elif isinstance(item, ToolCallRound):
            # Truncate very long tool results
            result_str = str(item.tool_result)
            if len(result_str) > 300:
                result_str = result_str[:300] + "... [truncated]"
            lines.append(f"Tool '{item.tool_name}' executed. Result: {result_str}")
            
    # 2. Add what the LLM just decided in the current response
    if payload.message_text:
        lines.append(f"Assistant: {payload.message_text.strip()}")
        
    for tool_call in payload.tool_calls:
        lines.append(f"Assistant Decision: Decided to call tool '{tool_call.name}' with arguments: {tool_call.arguments_json}")
        
    return "\n".join(lines)

async def _extract_gist(transcript: str, llm_profile: str) -> str:
    """Extract the core information from a conversation transcript using LLM."""
    try:
        client = create_llm_client(llm_profile)
        response = await client.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a summarizing assistant. Extract the core intent, key facts, tool "
                        "execution outcomes, and decisions from the conversation transcript. Return only "
                        "the extracted durable facts or decisions."
                    ),
                },
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ]
        )
        return response.strip()
    except Exception as e:
        logger.warning("Gist extraction failed: %s", e)
        return f"Summary fallback. Transcript snippet: {transcript[-200:]}"

async def execute_hook(
    hook_input: HookStageInput[AfterLlmResponseHookPayload],
) -> HookStageOutput[AfterLlmResponseHookPayload]:
    """Execute Hybrid Memory Storage Phase (Extract Gist & Store Vectors)."""
    payload = hook_input.payload
    config = _load_config()
    sid = payload.session_id
    llm_profile = _extraction_llm_profile(config)

    logger.debug(
        "hybrid_memory_extraction start: session_id=%s has_message_text=%s tool_calls=%d llm_profile=%s "
        "embedding_profile=%s",
        sid,
        bool(payload.message_text and str(payload.message_text).strip()),
        len(payload.tool_calls or []),
        llm_profile,
        config.get("embedding_profile"),
    )

    # Query database to get proper recent history including previous tools
    async with AsyncSessionLocal() as db:
        memory = PersistentMemory(EventRepository(db))
        history = await memory.get_history(payload.session_id, limit=10)

    transcript = _build_recent_transcript(history, payload)

    # If the LLM did not say anything and did not call any tools, skip.
    if not payload.message_text and not payload.tool_calls and not transcript.strip():
        logger.debug(
            "hybrid_memory_extraction skip: session_id=%s reason=empty_turn_no_transcript",
            sid,
        )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    logger.debug(
        "hybrid_memory_extraction transcript: session_id=%s history_items=%d transcript_chars=%d preview=%r",
        sid,
        len(history),
        len(transcript),
        _preview(transcript, 280),
    )

    # 1. Gist Extraction (Information summary from transcript)
    gist = await _extract_gist(transcript, llm_profile)
    if not gist.strip():
        logger.debug("hybrid_memory_extraction skip: session_id=%s reason=empty_gist_after_llm", sid)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    logger.debug(
        "hybrid_memory_extraction gist_ok: session_id=%s gist_chars=%d preview=%r",
        sid,
        len(gist),
        _preview(gist, 240),
    )

    # 2. Embedding
    try:
        embedding_profile = str(config["embedding_profile"])
        embedding_service = create_embedding_service(embedding_profile)
        embeddings = await embedding_service.embed([gist])
        if not embeddings:
            logger.debug(
                "hybrid_memory_extraction skip: session_id=%s reason=empty_embedding_response profile=%s",
                sid,
                embedding_profile,
            )
            return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
        vector = embeddings[0]
        logger.debug(
            "hybrid_memory_extraction embed_ok: session_id=%s profile=%s vector_dim=%d",
            sid,
            embedding_profile,
            len(vector),
        )
    except Exception as exc:
        logger.warning("Hybrid memory embedding failed: session_id=%s error=%s", payload.session_id, exc)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    # 3. Store into Vector DB
    try:
        store = LocalVectorStore(
            storage_path=str(config["vectorstore_path"]),
            collection_name=str(config["collection_name"]),
        )
        point_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).timestamp()

        # 构建存入向量库的数据结构
        point_data = {
            "id": point_id,
            "vector": vector,
            "payload": {
                "session_id": payload.session_id,
                "timestamp": timestamp,
                "transcript": transcript,
                "gist": gist,
                "source": "hybrid_memory_extraction"
            }
        }
        
        # 准备打印日志用的数据（截断过长的 vector 以防刷屏，同时确保 JSON 序列化安全）
        safe_vector = [float(v) for v in point_data["vector"][:3]] + ["..."] if len(point_data["vector"]) > 3 else [float(v) for v in point_data["vector"]]
        
        log_data = {
            "id": point_data["id"],
            "vector": safe_vector,
            "payload": point_data["payload"]
        }
        logger.info("Generated Hybrid Memory Data Structure:\n%s", json.dumps(log_data, ensure_ascii=False, indent=4).replace('"...\"', '...'))

        store.upsert(
            points=[point_data],
            vector_size=len(vector)
        )
        logger.info("Hybrid memory stored locally for session %s", payload.session_id)
        logger.debug(
            "hybrid_memory_extraction stored: session_id=%s point_id=%s collection=%s vector_dim=%d",
            sid,
            point_id,
            config.get("collection_name"),
            len(vector),
        )
    except Exception as exc:
        logger.warning(
            "Hybrid memory vector upsert failed: session_id=%s error=%s",
            payload.session_id,
            exc,
            exc_info=True,
        )

    return HookStageOutput(status=HookStatus.OK, patched_payload=payload, artifacts={"hybrid_memory": "stored"})

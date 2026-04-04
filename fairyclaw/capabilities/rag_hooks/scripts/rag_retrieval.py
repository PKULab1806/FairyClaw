# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""RAG retrieval hook backed by capability-local vector storage."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from fairyclaw.config.loader import load_yaml
from fairyclaw.core.agent.constants import SUB_SESSION_MARKER
from fairyclaw.core.agent.hooks.protocol import BeforeLlmCallHookPayload, HookStageInput, HookStageOutput, HookStatus, LlmChatMessage
from fairyclaw.infrastructure.embedding.service import create_embedding_service, load_embedding_profile
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter
from fairyclaw.capabilities.rag_hooks.scripts._vectorstore import LocalVectorStore

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
DEFAULT_CONFIG = {
    "top_k": 5,
    "token_cap": 1024,
    "similarity_threshold": 0.65,
    "embedding_profile": "embedding",
    "vectorstore_path": "./data/vectorstore",
    "collection_name": "fairyclaw_memory",
}


async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    """Recall relevant memory facts and inject them as a system message."""
    payload = hook_input.payload
    if payload.turn.user_turn is None:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload, artifacts={"retrieved_docs": []})

    query_text = payload.turn.user_turn.message.as_plain_text().strip()
    if not query_text:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload, artifacts={"retrieved_docs": []})

    config = _load_config()
    try:
        embedding_service = create_embedding_service(str(config["embedding_profile"]))
        embedding_profile = load_embedding_profile(str(config["embedding_profile"]))
        query_vector = (await embedding_service.embed([query_text]))[0]
        store = LocalVectorStore(
            storage_path=str(config["vectorstore_path"]),
            collection_name=str(config["collection_name"]),
        )
        results = store.search(
            query_vector=query_vector,
            limit=int(config["top_k"]),
            session_scope=_candidate_session_ids(payload.turn.session_id),
            score_threshold=float(config["similarity_threshold"]),
            query_text=query_text,
        )
    except Exception as exc:
        logger.warning("rag_retrieval skipped: session_id=%s error=%s", hook_input.context.session_id, exc)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload, artifacts={"retrieved_docs": []})

    if not results:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload, artifacts={"retrieved_docs": []})

    rag_text = _build_rag_text(results)
    rag_text = _fit_to_token_cap(rag_text, int(config["token_cap"]), counter=TokenCounter(model="gpt-4"))
    if not rag_text.strip():
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload, artifacts={"retrieved_docs": []})

    injected_message = LlmChatMessage(role="system", content=rag_text)
    llm_messages = list(payload.turn.llm_messages)
    if llm_messages and llm_messages[0].role == "system":
        llm_messages = [llm_messages[0], injected_message, *llm_messages[1:]]
    else:
        llm_messages = [injected_message, *llm_messages]

    patched_payload = replace(
        payload,
        turn=replace(
            payload.turn,
            llm_messages=llm_messages,
        ),
    )
    return HookStageOutput(
        status=HookStatus.OK,
        patched_payload=patched_payload,
        artifacts={
            "retrieved_docs": [
                {
                    "id": result["id"],
                    "score": result["score"],
                    "text": result["payload"].get("text", ""),
                    "category": result["payload"].get("category", ""),
                }
                for result in results
            ],
            "embedding_dimensions": embedding_profile.dimensions or len(query_vector),
        },
    )


def _load_config() -> dict[str, object]:
    raw = load_yaml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    return config


def _candidate_session_ids(session_id: str) -> list[str]:
    if SUB_SESSION_MARKER not in session_id:
        return [session_id]
    root_session = session_id.split(SUB_SESSION_MARKER, 1)[0]
    return [session_id, root_session]


def _build_rag_text(results: list[dict[str, object]]) -> str:
    lines = ["[RecalledMemory]"]
    for index, result in enumerate(results, start=1):
        payload = result["payload"]
        if not isinstance(payload, dict):
            continue
        category = str(payload.get("category", "fact"))
        text = str(payload.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"{index}. ({category}) {text}")
    lines.append("[/RecalledMemory]")
    return "\n".join(lines)


def _fit_to_token_cap(text: str, token_cap: int, counter: TokenCounter) -> str:
    if token_cap <= 0 or counter.count_text(text) <= token_cap:
        return text
    low = 0
    high = len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = f"{text[:mid].rstrip()}\n...[truncated]\n[/RecalledMemory]"
        if counter.count_text(candidate) <= token_cap:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


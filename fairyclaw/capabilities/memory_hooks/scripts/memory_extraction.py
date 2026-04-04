# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Memory extraction hook using heuristic fact mining."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fairyclaw.capabilities.memory_hooks.scripts._vectorstore import LocalVectorStore
from fairyclaw.config.loader import load_yaml
from fairyclaw.core.agent.context.history_ir import SessionMessageBlock
from fairyclaw.core.agent.hooks.protocol import AfterLlmResponseHookPayload, HookStageInput, HookStageOutput, HookStatus
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.infrastructure.database.repository import EventRepository, RagRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.infrastructure.embedding.service import create_embedding_service
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
DEFAULT_CONFIG = {
    "extraction_mode": "heuristic",
    "embedding_profile": "embedding",
    "vectorstore_path": "./data/vectorstore",
    "collection_name": "fairyclaw_memory",
}
PATH_PATTERN = re.compile(r"(?:/[\w.\-]+)+")
URL_PATTERN = re.compile(r"https?://[^\s)]+")
MARKER_PATTERN = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+){1,}\b")
PREFERENCE_HINTS = (
    "prefer",
    "please use",
    "always",
    "不要",
    "请使用",
    "优先",
)


async def execute_hook(
    hook_input: HookStageInput[AfterLlmResponseHookPayload],
) -> HookStageOutput[AfterLlmResponseHookPayload]:
    """Extract durable facts from recent conversation context."""
    payload = hook_input.payload
    config = _load_config()
    if str(config["extraction_mode"]).lower() != "heuristic":
        logger.debug("memory_extraction skipped: session_id=%s reason=non_heuristic_mode", payload.session_id)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async with AsyncSessionLocal() as db:
        memory = PersistentMemory(EventRepository(db))
        history = await memory.get_history(payload.session_id, limit=20)
        facts = _extract_facts(history=history, assistant_text=payload.message_text or "", payload=payload)
        if not facts:
            return HookStageOutput(
                status=HookStatus.SKIP,
                patched_payload=payload,
                artifacts={"memory_facts": []},
            )

        try:
            embedding_service = create_embedding_service(str(config["embedding_profile"]))
            embeddings = await embedding_service.embed([fact["text"] for fact in facts])
        except Exception as exc:
            logger.warning("memory_extraction skipped embedding: session_id=%s error=%s", payload.session_id, exc)
            return HookStageOutput(
                status=HookStatus.SKIP,
                patched_payload=payload,
                artifacts={"memory_facts": facts, "embedding_error": str(exc)},
            )

        rag_repo = RagRepository(db)
        document = await rag_repo.create_document(
            session_id=payload.session_id,
            source_type="memory_fact",
            source_ref=hook_input.context.turn_id,
            status="indexed",
            meta={"source": "memory_extraction", "mode": "heuristic"},
        )
        counter = TokenCounter(model="gpt-4")
        chunks = await rag_repo.insert_chunks(
            document_id=document.id,
            session_id=payload.session_id,
            chunks=[
                {
                    "text": fact["text"],
                    "token_count": counter.count_text(fact["text"]),
                    "embedding": vector,
                    "meta": {
                        "category": fact["category"],
                        "entities": fact["entities"],
                        "source": "memory_extraction",
                        "turn_id": hook_input.context.turn_id,
                        "session_id": payload.session_id,
                    },
                }
                for fact, vector in zip(facts, embeddings, strict=False)
            ],
        )
        try:
            store = LocalVectorStore(
                storage_path=str(config["vectorstore_path"]),
                collection_name=str(config["collection_name"]),
            )
            store.upsert(
                points=[
                    {
                        "id": chunk.id,
                        "vector": vector,
                        "payload": {
                            "text": fact["text"],
                            "category": fact["category"],
                            "entities": fact["entities"],
                            "source": "memory_extraction",
                            "turn_id": hook_input.context.turn_id,
                            "session_id": payload.session_id,
                        },
                    }
                    for chunk, fact, vector in zip(chunks, facts, embeddings, strict=False)
                ],
                vector_size=len(embeddings[0]) if embeddings else 0,
            )
        except Exception as exc:
            logger.warning("memory_extraction vector upsert failed: session_id=%s error=%s", payload.session_id, exc)
            return HookStageOutput(
                status=HookStatus.OK,
                patched_payload=payload,
                artifacts={"memory_facts": facts, "vectorstore_error": str(exc)},
            )

    return HookStageOutput(status=HookStatus.OK, patched_payload=payload, artifacts={"memory_facts": facts})


def _load_config() -> dict[str, object]:
    raw = load_yaml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    return config


def _extract_facts(
    history: list[object],
    assistant_text: str,
    payload: AfterLlmResponseHookPayload,
) -> list[dict[str, object]]:
    latest_user_text = _latest_user_text(history)
    facts: list[dict[str, object]] = []
    if latest_user_text:
        facts.extend(_extract_user_facts(latest_user_text))
    if assistant_text.strip():
        facts.extend(_extract_assistant_facts(assistant_text))
    for tool_call in payload.tool_calls:
        facts.append(
            {
                "text": f"Assistant decided to call tool '{tool_call.name}' with arguments {tool_call.arguments_json}.",
                "category": "decision",
                "entities": [tool_call.name],
            }
        )
    deduped: list[dict[str, object]] = []
    seen_texts: set[str] = set()
    for fact in facts:
        text = str(fact["text"]).strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        deduped.append({"text": text, "category": fact["category"], "entities": list(dict.fromkeys(fact["entities"]))})
    return deduped[:12]


def _latest_user_text(history: list[object]) -> str:
    for item in reversed(history):
        if isinstance(item, SessionMessageBlock) and item.role.value == "user":
            return item.as_plain_text().strip()
    return ""


def _extract_user_facts(text: str) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    entities = _extract_entities(text)
    marker_entities = MARKER_PATTERN.findall(text)
    path_entities = PATH_PATTERN.findall(text)
    url_entities = URL_PATTERN.findall(text)
    lowered = text.lower()
    if any(hint in lowered for hint in PREFERENCE_HINTS):
        facts.append({"text": text.strip(), "category": "preference", "entities": entities})
    if marker_entities or path_entities or url_entities:
        facts.append({"text": text.strip(), "category": "fact", "entities": entities})
    for marker in marker_entities:
        facts.append({"text": f"User referenced marker: {marker}", "category": "fact", "entities": [marker]})
    for path in path_entities:
        facts.append({"text": f"User referenced file or path: {path}", "category": "fact", "entities": [path]})
    for url in url_entities:
        facts.append({"text": f"User referenced URL: {url}", "category": "fact", "entities": [url]})
    return facts


def _extract_assistant_facts(text: str) -> list[dict[str, object]]:
    stripped = text.strip()
    if not stripped:
        return []
    entities = _extract_entities(stripped)
    lowered = stripped.lower()
    if not entities and not any(hint in lowered for hint in PREFERENCE_HINTS):
        return []
    return [
        {
            "text": stripped,
            "category": "decision",
            "entities": entities,
        }
    ]


def _extract_entities(text: str) -> list[str]:
    entities = MARKER_PATTERN.findall(text) + PATH_PATTERN.findall(text) + URL_PATTERN.findall(text)
    return list(dict.fromkeys(entity.strip() for entity in entities if entity.strip()))

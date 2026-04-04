# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Memory pre-context hook implementing Hybrid Memory Retrieval & Assembly Phase."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from fairyclaw.capabilities.memory_hooks.scripts._vectorstore import LocalVectorStore
from fairyclaw.config.loader import load_yaml
from fairyclaw.core.agent.hooks.protocol import BeforeLlmCallHookPayload, HookStageInput, HookStageOutput, HookStatus, LlmChatMessage
from fairyclaw.infrastructure.embedding.service import create_embedding_service
from fairyclaw.infrastructure.llm.factory import create_llm_client
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

DEFAULT_CONFIG = {
    "extraction_mode": "hybrid",
    "extraction_profile": "compaction_summarizer",
    "compaction_profile": "compaction_summarizer",
    "query_summarize_tool_results": False,
    "query_tool_char_threshold": 800,
    "query_tool_truncate_chars": 400,
    "embedding_profile": "embedding",
    "vectorstore_path": "./data/vectorstore",
    "collection_name": "fairyclaw_memory",
    "recent_n_messages": 5,
    "similar_m_messages": 3,
}

def _load_config() -> dict[str, object]:
    raw = load_yaml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    return config


def _preview(text: str, limit: int = 240) -> str:
    """Single-line preview for debug logs."""
    one = " ".join(str(text or "").split())
    if len(one) <= limit:
        return one
    return f"{one[: limit - 3]}..."

async def _summarize_long_tool_result(text: str, llm_profile: str) -> str:
    """Optional LLM shorten for retrieval query (only when query_summarize_tool_results is enabled)."""
    try:
        client = create_llm_client(llm_profile)
        response = await client.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful summarizer. Summarize the following tool execution result "
                        "into a concise description of its key facts, outcomes, or errors, keeping it "
                        "under 300 characters. Return only the summary."
                    ),
                },
                {"role": "user", "content": text[:3000]},
            ]
        )
        return response.strip()
    except Exception as e:
        logger.warning("Summarization of tool result failed: %s", e)
        return text[:300] + "... [truncated]"


def _shorten_for_query_embedding(text: str, config: dict[str, object]) -> str:
    """Clamp long message text for embedding the retrieval query without an extra LLM round-trip."""
    threshold = int(config.get("query_tool_char_threshold", 800))
    cap = int(config.get("query_tool_truncate_chars", 400))
    if len(text) <= threshold:
        return text
    if cap <= 0:
        return text[:threshold]
    return text[:cap] + ("..." if len(text) > cap else "")


async def _build_query_context(payload: BeforeLlmCallHookPayload, config: dict[str, object]) -> str:
    """Build a search query based on the most recent context (user or tool results)."""
    # If there is an explicit user_turn, include it
    user_text = ""
    if payload.turn.user_turn:
        user_text = payload.turn.user_turn.message.as_plain_text().strip()
        
    lines = []
    # Inspect the most recent LLM messages (which includes recent tool outputs and user messages)
    # We look backwards to collect up to ~1000 chars of recent context
    char_count = 0
    for msg in reversed(payload.turn.llm_messages):
        if msg.role == "system":
            continue
            
        content = getattr(msg, "content", "")
        if not content:
            continue
            
        text = str(content).strip()
        if text:
            use_llm = bool(config.get("query_summarize_tool_results", False))
            threshold = int(config.get("query_tool_char_threshold", 800))
            if len(text) > threshold:
                profile = str(config.get("compaction_profile") or config.get("extraction_profile") or "").strip()
                if use_llm and profile:
                    text = await _summarize_long_tool_result(text, profile)
                else:
                    text = _shorten_for_query_embedding(text, config)
            lines.append(f"{msg.role.capitalize()}: {text}")
            char_count += len(text)
            
        if char_count >= 1000:
            break
            
    lines.reverse()
    
    recent_context = "\n".join(lines)
    if user_text and user_text not in recent_context:
        recent_context += f"\nUser: {user_text}"
        
    return recent_context.strip() or "General conversation"

async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    """Retrieve vectors and assemble sliding window + long-term memory."""
    payload = hook_input.payload
    token_budget = payload.token_budget or hook_input.context.token_budget or 4000
    config = _load_config()
    sid = payload.turn.session_id
    n_in = len(payload.turn.llm_messages)

    if token_budget <= 0:
        logger.debug(
            "hybrid_memory_pre_context skip: session_id=%s reason=token_budget_non_positive budget=%s",
            sid,
            token_budget,
        )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    # 1. New Prompt Embedding (Build search query from recent tool or user context)
    query_text = await _build_query_context(payload, config)

    recent_n = int(config.get("recent_n_messages", 5))
    similar_m = int(config.get("similar_m_messages", 3))

    logger.debug(
        "hybrid_memory_pre_context start: session_id=%s llm_messages_in=%d token_budget=%s recent_n=%d similar_m=%s "
        "query_preview=%r",
        sid,
        n_in,
        token_budget,
        recent_n,
        similar_m,
        _preview(query_text, 300),
    )

    embedding_profile = str(config["embedding_profile"])
    try:
        embedding_service = create_embedding_service(embedding_profile)
        embeddings = await embedding_service.embed([query_text])
        if not embeddings:
            logger.debug(
                "hybrid_memory_pre_context skip: session_id=%s reason=empty_embedding_response profile=%s",
                sid,
                embedding_profile,
            )
            return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
        query_vector = embeddings[0]
        logger.debug(
            "hybrid_memory_pre_context embed_ok: session_id=%s profile=%s query_chars=%d vector_dim=%d",
            sid,
            embedding_profile,
            len(query_text),
            len(query_vector),
        )
    except Exception as exc:
        logger.warning("Failed to embed user prompt for retrieval: %s", exc)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    # 2. Retrieve Top-M (with strict session filter)
    try:
        store = LocalVectorStore(
            storage_path=str(config["vectorstore_path"]),
            collection_name=str(config["collection_name"]),
        )
        # Search by session tracking, fetching potentially more just in case of overlaps
        top_k_records = store.search(
            query_vector=query_vector,
            limit=similar_m + recent_n,
            filter_payload={"session_id": payload.turn.session_id},
        )
    except Exception as exc:
        logger.warning("Vector search failed: %s", exc)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    raw_hit_count = len(top_k_records)
    top_scores = [float(h.get("score", 0.0)) for h in top_k_records[:5]]
    logger.debug(
        "hybrid_memory_pre_context search: session_id=%s raw_hits=%d top_scores=%s collection=%s",
        sid,
        raw_hit_count,
        top_scores,
        config.get("collection_name"),
    )

    # 3. Context Truncation & Deduplication

    # Force truncate the incoming generic messages list to only the Recent N Messages
    # System prompt is ALWAYS kept at [0], then we take the last N items.
    system_prompts = [msg for msg in payload.turn.llm_messages if getattr(msg, "role", "") == "system"]
    other_msgs = [msg for msg in payload.turn.llm_messages if getattr(msg, "role", "") != "system"]

    recent_other_msgs = other_msgs[-recent_n:] if recent_n > 0 else []
    incoming_messages = system_prompts + recent_other_msgs

    # Grab short term strings for deduplication
    short_term_strings = {
        msg.content.strip()
        for msg in incoming_messages
        if hasattr(msg, "content") and msg.content
    }

    deduplicated_gists: list[str] = []
    dup_skipped = 0
    empty_gist_skipped = 0
    # Only keep the top M similar messages that are not already in the recent N messages
    for hit in top_k_records:
        if len(deduplicated_gists) >= similar_m:
            break
        hit_payload = hit.get("payload", {})
        gist = hit_payload.get("gist", "")
        if not gist:
            empty_gist_skipped += 1
            continue
        if gist in short_term_strings:
            dup_skipped += 1
            continue
        deduplicated_gists.append(str(gist))

    if not deduplicated_gists:
        if raw_hit_count == 0:
            logger.debug(
                "hybrid_memory_pre_context skip: session_id=%s reason=no_vector_hits_for_session "
                "system_msgs=%d other_msgs_total=%d recent_n_kept=%d",
                sid,
                len(system_prompts),
                len(other_msgs),
                len(recent_other_msgs),
            )
        else:
            logger.debug(
                "hybrid_memory_pre_context skip: session_id=%s reason=no_gists_after_dedup raw_hits=%d "
                "dup_skipped=%d empty_payload_gist=%d system_msgs=%d recent_n_kept=%d",
                sid,
                raw_hit_count,
                dup_skipped,
                empty_gist_skipped,
                len(system_prompts),
                len(recent_other_msgs),
            )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    # Token counting logic to fit memory into budget
    counter = TokenCounter(model="gpt-4")
    current_tokens = counter.count_messages(incoming_messages)
    tools_tokens = counter.count_json([tool.to_openai_tool() for tool in payload.tools]) if payload.tools else 0
    available_tokens = token_budget - current_tokens - tools_tokens

    logger.debug(
        "hybrid_memory_pre_context budget: session_id=%s gists_kept=%d tokens_messages=%d tokens_tools=%s "
        "token_budget=%d available_for_memory=%d",
        sid,
        len(deduplicated_gists),
        current_tokens,
        tools_tokens,
        token_budget,
        available_tokens,
    )

    if available_tokens <= 20:
        logger.debug(
            "hybrid_memory_pre_context skip: session_id=%s reason=insufficient_token_headroom available=%s",
            sid,
            available_tokens,
        )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    memory_chunks = []
    for gist in deduplicated_gists:
        chunk = f"[Relevant_History: {gist}]"
        chunk_tokens = counter.count_text(chunk)
        if available_tokens - chunk_tokens >= 0:
            memory_chunks.append(chunk)
            available_tokens -= chunk_tokens
        else:
            break

    if not memory_chunks:
        logger.debug(
            "hybrid_memory_pre_context skip: session_id=%s reason=memory_chunks_empty_after_token_fit",
            sid,
        )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    long_term_content = "\n".join(memory_chunks)
    memory_message = LlmChatMessage(
        role="system",
        content=f"<Relevant_History>\n{long_term_content}\n</Relevant_History>",
    )

    # Insert right after system prompt, or at top
    if incoming_messages and incoming_messages[0].role == "system":
        final_messages = [incoming_messages[0], memory_message] + incoming_messages[1:]
    else:
        final_messages = [memory_message] + incoming_messages

    patched_payload = replace(payload, turn=replace(payload.turn, llm_messages=final_messages))

    n_out = len(final_messages)
    logger.debug(
        "hybrid_memory_pre_context applied: session_id=%s llm_messages %d->%d system_blocks=%d "
        "injected_gists=%d memory_chunks=%d gist_previews=%s",
        sid,
        n_in,
        n_out,
        sum(1 for m in final_messages if getattr(m, "role", "") == "system"),
        len(deduplicated_gists),
        len(memory_chunks),
        [_preview(g, 80) for g in deduplicated_gists[:3]],
    )

    return HookStageOutput(
        status=HookStatus.OK,
        patched_payload=patched_payload,
        artifacts={"hybrid_retrieved": len(memory_chunks)},
    )

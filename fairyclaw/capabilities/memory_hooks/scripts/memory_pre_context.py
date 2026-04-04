# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Memory pre-context hook for compaction summary injection."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from pathlib import Path

from fairyclaw.config.loader import load_yaml
from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SessionMessageBlock, ToolCallRound
from fairyclaw.core.agent.hooks.protocol import BeforeLlmCallHookPayload, HookStageInput, HookStageOutput, HookStatus, LlmChatMessage
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.infrastructure.llm.factory import create_llm_client
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
THINK_BLOCK_PATTERN = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
THINK_TAG_PATTERN = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
SUMMARY_SECTION_MARKERS = (
    "User intent:",
    "Changes made:",
    "Key decisions:",
    "Next steps:",
)
DEFAULT_CONFIG = {
    "compaction_profile": "compaction_summarizer",
    "compaction_max_history_items": 120,
    "compaction_min_history_items": 12,
    "summary_char_limit": 1500,
}


async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    """Inject a compacted session summary after compression."""
    payload = hook_input.payload
    token_budget = payload.token_budget or hook_input.context.token_budget or 0
    if token_budget <= 0:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    config = _load_config()
    async with AsyncSessionLocal() as db:
        memory = PersistentMemory(EventRepository(db))
        snapshot = await memory.get_latest_compaction(payload.turn.session_id)
        if snapshot is None:
            snapshot = await _maybe_create_snapshot(
                hook_input=hook_input,
                memory=memory,
                config=config,
            )

    if snapshot is None or not snapshot.summary_text.strip():
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    summary_source = _sanitize_compaction_summary(snapshot.summary_text)
    if not summary_source.strip():
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    counter = TokenCounter(model="gpt-4")
    tools_tokens = counter.count_json([tool.to_openai_tool() for tool in payload.tools])
    current_tokens = counter.count_messages(payload.turn.llm_messages) + tools_tokens
    available = token_budget - current_tokens
    if available <= 32:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    summary_text = _fit_summary_to_budget(
        text=summary_source,
        available_tokens=available,
        counter=counter,
        char_limit=int(config["summary_char_limit"]),
    )
    if not summary_text.strip():
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    memory_message = LlmChatMessage(
        role="system",
        content=f"[SessionCompaction]\n{summary_text}\n[/SessionCompaction]",
    )
    llm_messages = list(payload.turn.llm_messages)
    if llm_messages and llm_messages[0].role == "system":
        llm_messages = [llm_messages[0], memory_message, *llm_messages[1:]]
    else:
        llm_messages = [memory_message, *llm_messages]

    patched_payload = replace(payload, turn=replace(payload.turn, llm_messages=llm_messages))
    return HookStageOutput(
        status=HookStatus.OK,
        patched_payload=patched_payload,
        artifacts={"compaction_summary": summary_text},
    )


def _load_config() -> dict[str, object]:
    raw = load_yaml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    return config


async def _maybe_create_snapshot(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
    memory: PersistentMemory,
    config: dict[str, object],
):
    history_limit = int(config["compaction_max_history_items"])
    history = await memory.get_history(hook_input.context.session_id, limit=history_limit)
    if len(history) < int(config["compaction_min_history_items"]):
        return None
    transcript = _history_to_transcript(history)
    if not transcript.strip():
        return None

    summary_text = await _summarize_history(
        transcript=transcript,
        profile_name=str(config["compaction_profile"]),
    )
    summary_text = _sanitize_compaction_summary(summary_text)
    if not summary_text.strip():
        return None

    key_facts = _extract_compaction_facts(history)
    return await memory.create_compaction_snapshot(
        session_id=hook_input.context.session_id,
        strategy="anchored_summary",
        summary_text=summary_text,
        key_facts=key_facts,
        created_by="memory_pre_context",
    )


async def _summarize_history(transcript: str, profile_name: str) -> str:
    try:
        client = create_llm_client(profile_name)
        return (
            await client.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarize the conversation into a durable memory anchor. "
                            "Keep file paths, URLs, errors, decisions, and unresolved tasks. "
                            "Return only the final summary. Do not include analysis, reasoning, "
                            "or any <think> tags."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Summarize the following transcript with sections:\n"
                            "- User intent\n"
                            "- Changes made\n"
                            "- Key decisions\n"
                            "- Next steps\n\n"
                            "Output only the final summary using those section titles. "
                            "Do not emit hidden reasoning, preambles, or <think> blocks.\n\n"
                            f"{transcript}"
                        ),
                    },
                ]
            )
        ).strip()
    except Exception as exc:
        logger.warning("memory_pre_context summarizer failed: profile=%s error=%s", profile_name, exc)
        return ""


def _sanitize_compaction_summary(text: str) -> str:
    """Strip model reasoning artifacts and keep only durable summary content."""
    cleaned = THINK_BLOCK_PATTERN.sub("", text or "")
    cleaned = THINK_TAG_PATTERN.sub("", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return ""
    positions = [cleaned.find(marker) for marker in SUMMARY_SECTION_MARKERS if cleaned.find(marker) >= 0]
    if positions:
        cleaned = cleaned[min(positions) :].lstrip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _history_to_transcript(history: list[ChatHistoryItem]) -> str:
    lines: list[str] = []
    for item in history:
        if isinstance(item, SessionMessageBlock):
            text = item.as_plain_text().strip()
            if text:
                lines.append(f"{item.role.value}: {text}")
            continue
        lines.append(
            "tool: "
            + json.dumps(
                {
                    "tool_name": item.tool_name,
                    "arguments_json": item.arguments_json,
                    "tool_result": item.tool_result,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def _extract_compaction_facts(history: list[ChatHistoryItem]) -> dict[str, object]:
    recent_messages = [item.as_plain_text() for item in history if isinstance(item, SessionMessageBlock)][-6:]
    recent_tools = [item.tool_name for item in history if isinstance(item, ToolCallRound)][-6:]
    return {
        "recent_messages": recent_messages,
        "recent_tools": recent_tools,
    }


def _fit_summary_to_budget(text: str, available_tokens: int, counter: TokenCounter, char_limit: int) -> str:
    limited = text[:char_limit].strip()
    if available_tokens <= 0:
        return ""
    if counter.count_text(limited) <= available_tokens:
        return limited
    low = 0
    high = len(limited)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = f"{limited[:mid].rstrip()}\n...[truncated]"
        if counter.count_text(candidate) <= available_tokens:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


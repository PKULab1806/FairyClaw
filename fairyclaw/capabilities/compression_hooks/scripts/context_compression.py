# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Context compression hook for prompt budgeting."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from fairyclaw.config.loader import load_yaml
from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SessionMessageBlock, ToolCallRound
from fairyclaw.core.agent.context.llm_message_assembler import LlmMessageAssembler
from fairyclaw.core.agent.hooks.protocol import (
    BeforeLlmCallHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
    LlmChatMessage,
)
from fairyclaw.core.agent.types import SystemPromptPart
from fairyclaw.core.domain import ContentSegment
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter

logger = logging.getLogger(__name__)
MESSAGE_ASSEMBLER = LlmMessageAssembler()
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
DEFAULT_CONFIG = {
    "recency_window": 6,
    "tool_result_max_chars": 500,
    "assistant_message_max_chars": 1200,
}


async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    """Compress history until the prompt fits the current token budget."""
    payload = hook_input.payload
    token_budget = payload.token_budget or hook_input.context.token_budget or 0
    if token_budget <= 0:
        logger.debug("context_compression skipped: session_id=%s reason=no_budget", hook_input.context.session_id)
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    config = _load_config()
    counter = TokenCounter(model="gpt-4")
    system_prompt, extra_system_messages = _extract_system_messages(payload)
    original_tokens = _count_prompt_tokens(counter, payload.turn.llm_messages, payload)
    if original_tokens <= token_budget:
        logger.debug(
            "context_compression skipped: session_id=%s reason=under_token_budget tokens=%s budget=%s",
            payload.turn.session_id,
            original_tokens,
            token_budget,
        )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    compressed_history = _truncate_large_items(
        history_items=payload.turn.history_items,
        tool_result_max_chars=int(config["tool_result_max_chars"]),
        assistant_message_max_chars=int(config["assistant_message_max_chars"]),
    )
    if _count_rebuilt_prompt(counter, payload, compressed_history, system_prompt, extra_system_messages) <= token_budget:
        return _build_output(
            payload,
            compressed_history,
            system_prompt,
            extra_system_messages,
            counter=counter,
            original_tokens=original_tokens,
            token_budget=token_budget,
        )

    compressed_history = _keep_recent_history(
        history_items=compressed_history,
        recent_message_limit=int(config["recency_window"]),
    )
    if _count_rebuilt_prompt(counter, payload, compressed_history, system_prompt, extra_system_messages) <= token_budget:
        return _build_output(
            payload,
            compressed_history,
            system_prompt,
            extra_system_messages,
            counter=counter,
            original_tokens=original_tokens,
            token_budget=token_budget,
        )

    compressed_history = [item for item in compressed_history if not isinstance(item, ToolCallRound)]
    if _count_rebuilt_prompt(counter, payload, compressed_history, system_prompt, extra_system_messages) <= token_budget:
        return _build_output(
            payload,
            compressed_history,
            system_prompt,
            extra_system_messages,
            counter=counter,
            original_tokens=original_tokens,
            token_budget=token_budget,
        )

    while compressed_history and _count_rebuilt_prompt(
        counter,
        payload,
        compressed_history,
        system_prompt,
        extra_system_messages,
    ) > token_budget:
        compressed_history = compressed_history[1:]

    return _build_output(
        payload,
        compressed_history,
        system_prompt,
        extra_system_messages,
        counter=counter,
        original_tokens=original_tokens,
        token_budget=token_budget,
    )


def _build_output(
    payload: BeforeLlmCallHookPayload,
    history_items: list[ChatHistoryItem],
    system_prompt: str,
    extra_system_messages: list[LlmChatMessage],
    *,
    counter: TokenCounter,
    original_tokens: int,
    token_budget: int,
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    """Rebuild provider-boundary messages from compressed history."""
    rebuilt_messages = _rebuild_messages(payload, history_items, system_prompt, extra_system_messages)
    new_tokens = _count_prompt_tokens(counter, rebuilt_messages, payload)
    if new_tokens < original_tokens or len(history_items) != len(payload.turn.history_items):
        logger.info(
            "context_compression applied: session_id=%s history_items=%d->%d tokens=%d->%d budget=%d",
            payload.turn.session_id,
            len(payload.turn.history_items),
            len(history_items),
            original_tokens,
            new_tokens,
            token_budget,
        )
    patched_payload = replace(
        payload,
        turn=replace(
            payload.turn,
            history_items=history_items,
            llm_messages=rebuilt_messages,
        ),
    )
    return HookStageOutput(status=HookStatus.OK, patched_payload=patched_payload)


def _load_config() -> dict[str, int]:
    """Load hook-local config with defaults."""
    raw = load_yaml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    return {
        "recency_window": int(raw.get("recency_window", DEFAULT_CONFIG["recency_window"])),
        "tool_result_max_chars": int(raw.get("tool_result_max_chars", DEFAULT_CONFIG["tool_result_max_chars"])),
        "assistant_message_max_chars": int(
            raw.get("assistant_message_max_chars", DEFAULT_CONFIG["assistant_message_max_chars"])
        ),
    }


def _extract_system_messages(payload: BeforeLlmCallHookPayload) -> tuple[str, list[LlmChatMessage]]:
    """Read the primary system prompt plus any injected system context."""
    if not payload.turn.llm_messages:
        return "", []
    messages = payload.turn.llm_messages
    first_message = messages[0]
    if first_message.role != "system":
        return "", []
    extra_messages: list[LlmChatMessage] = []
    index = 1
    while index < len(messages) and messages[index].role == "system":
        extra_messages.append(messages[index])
        index += 1
    prompt_text = first_message.content if isinstance(first_message.content, str) else ""
    return prompt_text, extra_messages


def _truncate_large_items(
    history_items: Iterable[ChatHistoryItem],
    tool_result_max_chars: int,
    assistant_message_max_chars: int,
) -> list[ChatHistoryItem]:
    """Clamp oversized tool results and assistant messages."""
    truncated: list[ChatHistoryItem] = []
    for item in history_items:
        if isinstance(item, ToolCallRound):
            truncated.append(
                replace(
                    item,
                    tool_result=_truncate_text(item.tool_result, tool_result_max_chars),
                    arguments_json=_truncate_text(item.arguments_json, tool_result_max_chars),
                )
            )
            continue
        if item.role.value == "assistant":
            truncated.append(_truncate_assistant_message(item, assistant_message_max_chars))
            continue
        truncated.append(item)
    return truncated


def _truncate_assistant_message(item: SessionMessageBlock, max_chars: int) -> SessionMessageBlock:
    """Clamp assistant text while preserving the message structure."""
    plain_text = item.as_plain_text()
    truncated_text = _truncate_text(plain_text, max_chars)
    if truncated_text == plain_text:
        return item
    rebuilt = SessionMessageBlock.from_segments(item.role, (ContentSegment.text_segment(truncated_text),))
    return rebuilt or item


def _keep_recent_history(history_items: list[ChatHistoryItem], recent_message_limit: int) -> list[ChatHistoryItem]:
    """Keep only a recent conversational window from the tail of history."""
    kept: deque[ChatHistoryItem] = deque()
    seen_message_blocks = 0
    for item in reversed(history_items):
        kept.appendleft(item)
        if isinstance(item, SessionMessageBlock):
            seen_message_blocks += 1
            if seen_message_blocks >= recent_message_limit:
                break
    return list(kept)


def _count_prompt_tokens(
    counter: TokenCounter,
    messages,
    payload: BeforeLlmCallHookPayload,
) -> int:
    """Count tokens for messages plus tool schemas."""
    return counter.count_messages(list(messages)) + counter.count_json([tool.to_openai_tool() for tool in payload.tools])


def _count_rebuilt_prompt(
    counter: TokenCounter,
    payload: BeforeLlmCallHookPayload,
    history_items: list[ChatHistoryItem],
    system_prompt: str,
    extra_system_messages: list[LlmChatMessage],
) -> int:
    """Count prompt tokens after rebuilding compressed history."""
    rebuilt_messages = _rebuild_messages(payload, history_items, system_prompt, extra_system_messages)
    return _count_prompt_tokens(counter, rebuilt_messages, payload)


def _rebuild_messages(
    payload: BeforeLlmCallHookPayload,
    history_items: list[ChatHistoryItem],
    system_prompt: str,
    extra_system_messages: list[LlmChatMessage],
) -> list[LlmChatMessage]:
    """Rebuild history while preserving earlier system-message injections."""
    rebuilt_messages = MESSAGE_ASSEMBLER.assemble(
        system_prompt=SystemPromptPart(text=system_prompt),
        history_entries=history_items,
        user_entry=payload.turn.user_turn,
    )
    if not extra_system_messages:
        return rebuilt_messages
    if rebuilt_messages and rebuilt_messages[0].role == "system":
        return [rebuilt_messages[0], *extra_system_messages, *rebuilt_messages[1:]]
    return [*extra_system_messages, *rebuilt_messages]


def _truncate_text(text: str, max_chars: int) -> str:
    """Clamp long text with an omission marker."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n...[truncated]"


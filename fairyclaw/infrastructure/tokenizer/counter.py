# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Token counting helpers for prompt budgeting."""

from __future__ import annotations

import json
from typing import Any

from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SessionMessageBlock, ToolCallRound
from fairyclaw.core.agent.hooks.protocol import LlmChatMessage, LlmToolCallRequest


class TokenCounter:
    """Count approximate or exact tokens for chat payloads."""

    @staticmethod
    def _load_encoding(model: str):  # type: ignore[no-untyped-def]
        try:
            import tiktoken

            try:
                return tiktoken.encoding_for_model(model)
            except KeyError:
                return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None

    def __init__(self, model: str = "gpt-4") -> None:
        self.model = model
        self._encoding = self._load_encoding(model)

    def count_text(self, text: str) -> int:
        """Count tokens for one plain-text payload."""
        normalized = text or ""
        if self._encoding is not None:
            return len(self._encoding.encode(normalized))
        # Rough fallback when tiktoken is unavailable.
        return max(1, len(normalized) // 4) if normalized else 0

    def _count_content(self, content: str | list[dict[str, object]] | None) -> int:
        if content is None:
            return 0
        if isinstance(content, str):
            return self.count_text(content)
        return self.count_json(content)

    def count_message(self, message: LlmChatMessage) -> int:
        """Count tokens for one LLM chat message."""
        total = self.count_text(message.role)
        total += self._count_content(message.content)
        if message.tool_call_id:
            total += self.count_text(message.tool_call_id)
        if message.name:
            total += self.count_text(message.name)
        if message.tool_calls:
            for tool_call in message.tool_calls:
                total += self.count_tool_call(tool_call)
        # Add a small envelope overhead per message.
        return total + 4

    def count_messages(self, messages: list[LlmChatMessage]) -> int:
        """Count tokens for a message sequence."""
        return sum(self.count_message(message) for message in messages)

    def count_history_item(self, item: ChatHistoryItem) -> int:
        """Count tokens for one typed history item."""
        if isinstance(item, SessionMessageBlock):
            return self.count_text(item.role.value) + self._count_content(item.as_openai_content()) + 4
        return (
            self.count_text(item.tool_name)
            + self.count_text(item.call_id)
            + self.count_text(item.arguments_json)
            + self.count_text(item.tool_result)
            + 6
        )

    def count_history(self, history_items: list[ChatHistoryItem]) -> int:
        """Count tokens for typed history entries."""
        return sum(self.count_history_item(item) for item in history_items)

    def count_tool_call(self, tool_call: LlmToolCallRequest) -> int:
        """Count tokens for one tool-call request."""
        return (
            self.count_text(tool_call.call_id)
            + self.count_text(tool_call.name)
            + self.count_text(tool_call.arguments_json)
            + 4
        )

    def count_json(self, value: Any) -> int:
        """Count tokens for a structured payload by serializing it."""
        return self.count_text(json.dumps(value, ensure_ascii=False, sort_keys=True))

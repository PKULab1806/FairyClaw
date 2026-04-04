# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Assemble typed planner entries into OpenAI-compatible message payloads."""

from __future__ import annotations

import re

from fairyclaw.core.agent.hooks.protocol import LlmChatMessage, LlmToolCallRequest
from fairyclaw.core.agent.types import SystemPromptPart

from .history_ir import ChatHistoryItem, SessionMessageBlock, ToolCallRound, UserTurn


class LlmMessageAssembler:
    """Build typed message payloads from typed planner entries."""

    def assemble(
        self,
        system_prompt: SystemPromptPart,
        history_entries: list[ChatHistoryItem],
        user_entry: UserTurn | None,
    ) -> list[LlmChatMessage]:
        """Assemble one typed message list for LLM tool-chat call."""
        messages: list[LlmChatMessage] = [LlmChatMessage(role="system", content=system_prompt.text)]
        index = 0
        while index < len(history_entries):
            entry = history_entries[index]
            if isinstance(entry, SessionMessageBlock):
                if entry.role.value == "assistant":
                    tool_rounds = self._collect_consecutive_tool_rounds(history_entries, start=index + 1)
                    if tool_rounds:
                        messages.append(
                            LlmChatMessage(
                                role="assistant",
                                content=entry.as_openai_content(),
                                tool_calls=[self._to_tool_call_request(round_) for round_ in tool_rounds],
                            )
                        )
                        messages.extend(self._tool_result_messages(tool_rounds))
                        index += 1 + len(tool_rounds)
                        continue
                messages.append(LlmChatMessage(role=entry.role.value, content=entry.as_openai_content()))
                index += 1
                continue

            tool_rounds = self._collect_consecutive_tool_rounds(history_entries, start=index)
            messages.append(
                LlmChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[self._to_tool_call_request(round_) for round_ in tool_rounds],
                )
            )
            messages.extend(self._tool_result_messages(tool_rounds))
            index += len(tool_rounds)
        if user_entry is not None:
            messages.append(
                LlmChatMessage(role=user_entry.message.role.value, content=user_entry.message.as_openai_content())
            )
        return messages

    def _collect_consecutive_tool_rounds(
        self,
        history_entries: list[ChatHistoryItem],
        start: int,
    ) -> list[ToolCallRound]:
        """Collect one consecutive tool-call batch from history."""
        rounds: list[ToolCallRound] = []
        index = start
        expected_next_ordinal: int | None = None
        while index < len(history_entries):
            entry = history_entries[index]
            if isinstance(entry, SessionMessageBlock):
                break
            current_ordinal = self._tool_call_ordinal(entry.call_id)
            if rounds:
                # Keep batching only when call ordinals keep growing inside one LLM turn.
                # If ordinal resets (e.g. tc_1_* again), it is likely a new turn and must not merge.
                if expected_next_ordinal is None or current_ordinal is None or current_ordinal != expected_next_ordinal:
                    break
            rounds.append(entry)
            expected_next_ordinal = (current_ordinal + 1) if current_ordinal is not None else None
            index += 1
        return rounds

    def _tool_call_ordinal(self, call_id: str) -> int | None:
        """Parse short call-id ordinal like `tc_2` / `tc_2_xxx` -> 2."""
        match = re.match(r"^tc_(\d+)(?:_|$)", str(call_id or ""))
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _to_tool_call_request(self, entry: ToolCallRound) -> LlmToolCallRequest:
        """Convert one tool round into one assistant tool-call item."""
        return LlmToolCallRequest(
            call_id=entry.call_id,
            name=entry.tool_name,
            arguments_json=entry.arguments_json,
        )

    def _tool_result_messages(self, entries: list[ToolCallRound]) -> list[LlmChatMessage]:
        """Convert one tool-call batch into tool result messages."""
        return [
            LlmChatMessage(
                role="tool",
                tool_call_id=entry.call_id,
                name=entry.tool_name,
                content=entry.tool_result,
            )
            for entry in entries
        ]

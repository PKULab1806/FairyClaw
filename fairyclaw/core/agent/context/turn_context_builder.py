# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Build one LLM turn context from persisted history and user segments."""

from __future__ import annotations

from fairyclaw.config.settings import settings
from fairyclaw.core.agent.constants import SUB_SESSION_MARKER
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.agent.hooks.protocol import LlmChatMessage
from fairyclaw.core.agent.types import SystemPromptPart

from .history_ir import ChatHistoryItem, SessionMessageBlock, SessionMessageRole, UserTurn
from .llm_message_assembler import LlmMessageAssembler
from .system_prompts import build_system_prompt


class TurnContextBuilder:
    """Assemble turn-level LLM messages from typed history IR."""

    def __init__(self, message_assembler: LlmMessageAssembler) -> None:
        self.message_assembler = message_assembler

    def build(
        self,
        history_items: list[ChatHistoryItem],
        user_segments: tuple[ContentSegment, ...],
        session_id: str,
        task_type: str,
        workspace_root: str | None = None,
    ) -> tuple[list[LlmChatMessage], list[ChatHistoryItem], UserTurn | None]:
        """Build typed LLM messages plus typed history/user IR for one turn."""
        nesting_depth = session_id.count(SUB_SESSION_MARKER)
        system_prompt = build_system_prompt(
            nesting_depth=nesting_depth,
            task_type=task_type,
            prompt_language=settings.system_prompt_language,
            workspace_root=workspace_root,
        )
        history_entries, user_entry = self._split_current_user_turn(
            history_items=list(history_items),
            explicit_user_turn=UserTurn.from_segments(user_segments),
        )
        messages = self.message_assembler.assemble(
            system_prompt=SystemPromptPart(text=system_prompt),
            history_entries=history_entries,
            user_entry=user_entry,
        )
        return messages, history_entries, user_entry

    def _split_current_user_turn(
        self,
        history_items: list[ChatHistoryItem],
        explicit_user_turn: UserTurn | None,
    ) -> tuple[list[ChatHistoryItem], UserTurn | None]:
        """Separate the current user turn from prior history when possible."""
        if not history_items:
            return history_items, explicit_user_turn

        last_item = history_items[-1]
        if not self._is_user_message(last_item):
            return history_items, explicit_user_turn

        if explicit_user_turn is not None:
            if last_item == explicit_user_turn.message:
                return history_items[:-1], explicit_user_turn
            return history_items, explicit_user_turn

        return history_items[:-1], UserTurn(message=last_item)

    def _is_user_message(self, item: ChatHistoryItem) -> bool:
        """Return whether a history item is a user-authored session block."""
        return isinstance(item, SessionMessageBlock) and item.role is SessionMessageRole.USER

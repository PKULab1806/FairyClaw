# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session-kind-specific planner turn policies."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Protocol

from fairyclaw.core.agent.session.session_role import resolve_session_role_policy
from fairyclaw.core.agent.types import SessionKind, TurnRequest

if TYPE_CHECKING:
    from .planner import Planner


class TurnExecutionPolicy(Protocol):
    """Planner policy hooks for one session kind."""

    kind: SessionKind

    async def should_skip(self, planner: Planner, request: TurnRequest) -> bool:
        """Return whether the current turn should be skipped."""

    async def handle_text_response(self, planner: Planner, request: TurnRequest, message_text: str | None) -> None:
        """Handle direct-text completion with no tool calls."""

    async def handle_tool_follow_up(
        self,
        planner: Planner,
        request: TurnRequest,
        task_type: str,
        resolved_groups: list[str],
        called_tools: list[str],
    ) -> None:
        """Handle post-tool follow-up behavior."""

    async def handle_failure(self, planner: Planner, request: TurnRequest, exc: Exception) -> None:
        """Handle planner failure for this session kind."""


class MainSessionTurnPolicy:
    """Main-session behavior for one planner turn."""

    kind = SessionKind.MAIN

    async def should_skip(self, planner: Planner, request: TurnRequest) -> bool:
        return False

    async def handle_text_response(self, planner: Planner, request: TurnRequest, message_text: str | None) -> None:
        if message_text:
            await planner._handle_text_fallback(
                request.session_id,
                message_text,
                request.memory,
            )

    async def handle_tool_follow_up(
        self,
        planner: Planner,
        request: TurnRequest,
        task_type: str,
        resolved_groups: list[str],
        called_tools: list[str],
    ) -> None:
        if planner._should_publish_follow_up(called_tools):
            await planner._publish_follow_up_event(request.session_id, task_type, resolved_groups)

    async def handle_failure(self, planner: Planner, request: TurnRequest, exc: Exception) -> None:
        planner.logger.error("Error in planner loop: %s\n%s", exc, traceback.format_exc())


class SubSessionTurnPolicy:
    """Sub-session behavior for one planner turn."""

    kind = SessionKind.SUB

    async def should_skip(self, planner: Planner, request: TurnRequest) -> bool:
        if not planner.subtasks.is_sub_session_terminal(request.session_id):
            return False
        planner.logger.info("Skip turn for terminal sub-session: session=%s", request.session_id)
        return True

    async def handle_text_response(self, planner: Planner, request: TurnRequest, message_text: str | None) -> None:
        if not message_text:
            return
        await planner._handle_text_fallback(
            request.session_id,
            message_text,
            request.memory,
        )
        role_policy = resolve_session_role_policy(request.session_id)
        if role_policy.should_auto_mark_terminal_on_text:
            await planner.subtasks.mark_subtask_if_non_terminal(request.session_id, "completed", message_text)
            await planner.subtasks.publish_subtask_barrier_if_ready(request.session_id)

    async def handle_tool_follow_up(
        self,
        planner: Planner,
        request: TurnRequest,
        task_type: str,
        resolved_groups: list[str],
        called_tools: list[str],
    ) -> None:
        if planner.subtasks.is_sub_session_terminal(request.session_id):
            planner.logger.info(
                "Skip follow-up publish for terminal sub-session: session=%s",
                request.session_id,
            )
            await planner.subtasks.publish_subtask_barrier_if_ready(request.session_id)
            return
        if planner._should_publish_follow_up(called_tools):
            await planner._publish_follow_up_event(request.session_id, task_type, resolved_groups)

    async def handle_failure(self, planner: Planner, request: TurnRequest, exc: Exception) -> None:
        planner.logger.error("Error in planner loop: %s\n%s", exc, traceback.format_exc())
        await planner.subtasks.mark_subtask_if_non_terminal(request.session_id, "failed", str(exc))
        status = planner.subtasks.lookup_subtask_status(request.session_id)
        if status == "failed":
            await planner.subtasks.notify_main_session_subtask_failure(request.session_id, str(exc), status=status)
        await planner.subtasks.publish_subtask_barrier_if_ready(request.session_id)

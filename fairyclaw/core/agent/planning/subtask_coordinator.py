# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Subtask state and notification coordinator for planner orchestration."""

from __future__ import annotations

import logging

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.core.agent.session.global_state import get_main_session_by_sub_session, get_or_create_subtask_state
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.events.bus import EventType
from fairyclaw.core.events.runtime import publish_runtime_event
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class SubtaskCoordinator:
    """Coordinate terminal transitions and main-session subtask notifications."""

    def is_sub_session_terminal(self, sub_session_id: str) -> bool:
        """Check whether a sub-session is already terminal in main-session state."""
        main_session_id = get_main_session_by_sub_session(sub_session_id)
        if not main_session_id:
            return False
        state = get_or_create_subtask_state(main_session_id)
        return state.is_terminal(sub_session_id)

    async def mark_subtask_if_non_terminal(self, sub_session_id: str, status: str, summary: str) -> None:
        """Mark subtask terminal only when it is not already terminal."""
        main_session_id = get_main_session_by_sub_session(sub_session_id)
        if not main_session_id:
            return
        state = get_or_create_subtask_state(main_session_id)
        if not state.is_terminal(sub_session_id):
            state.mark_terminal(sub_session_id, status, summary)

    def lookup_subtask_status(self, sub_session_id: str) -> str | None:
        """Look up current status for one sub-session record."""
        main_session_id = get_main_session_by_sub_session(sub_session_id)
        if not main_session_id:
            return None
        state = get_or_create_subtask_state(main_session_id)
        for record in state.list_records():
            if record.sub_session_id == sub_session_id:
                return record.status
        return None

    async def publish_subtask_barrier_if_ready(self, sub_session_id: str) -> None:
        """Publish aggregated barrier message when all subtasks are terminal."""
        main_session_id = get_main_session_by_sub_session(sub_session_id)
        if not main_session_id:
            return
        state = get_or_create_subtask_state(main_session_id)
        if not state.is_all_subtasks_terminal():
            return
        if state.has_aggregation_emitted():
            return
        aggregated = state.get_aggregated_subtask_results()
        summaries = aggregated.get("summaries", {})
        ordered = state.get_current_batch_records()
        blocks: list[str] = []
        for record in ordered:
            payload = summaries.get(record.sub_session_id)
            if not payload:
                continue
            blocks.append(f"Sub-agent ({record.sub_session_id}) [{record.status}]:\n{payload}")
        if not blocks:
            return

        combined_msg = "[System Notification] Background tasks completed:\n\n" + "\n\n---\n\n".join(blocks)
        try:
            async with AsyncSessionLocal() as db:
                repo = EventRepository(db)
                main_memory = PersistentMemory(repo)
                message = SessionMessageBlock.from_segments(
                    SessionMessageRole.USER,
                    (ContentSegment.text_segment(combined_msg),),
                )
                if message is None:
                    return
                await main_memory.add_session_event(
                    session_id=main_session_id,
                    message=message,
                )
            await publish_runtime_event(
                event_type=EventType.SUBTASK_COMPLETED,
                session_id=main_session_id,
                payload={"sub_session_id": sub_session_id, "aggregated": aggregated, "trigger_turn": True},
                source="subtask_barrier",
            )
            state.mark_aggregation_emitted()
            logger.info(
                "Sub-agent barrier published for main_session=%s, total=%s, completed=%s, failed=%s, cancelled=%s",
                main_session_id,
                aggregated.get("total", 0),
                aggregated.get("completed", 0),
                aggregated.get("failed", 0),
                aggregated.get("cancelled", 0),
            )
        except Exception as exc:
            logger.error("Failed to publish subtask barrier for session %s: %s", main_session_id, exc)

    async def notify_main_session_subtask_failure(
        self,
        sub_session_id: str,
        summary: str,
        status: str = "failed",
    ) -> None:
        """Notify main session immediately for one subtask failure."""
        main_session_id = get_main_session_by_sub_session(sub_session_id)
        if not main_session_id or status in {"cancelled", "completed"}:
            return
        state = get_or_create_subtask_state(main_session_id)
        if state.has_immediate_failure_notified(sub_session_id):
            return

        failure_summary = summary.strip() or "Sub-agent exited with an unknown error."
        failure_msg = (
            "[System Notification] Sub-agent failed early:\n\n"
            f"Sub-agent ({sub_session_id}) [failed]:\n{failure_summary}"
        )
        try:
            async with AsyncSessionLocal() as db:
                repo = EventRepository(db)
                main_memory = PersistentMemory(repo)
                message = SessionMessageBlock.from_segments(
                    SessionMessageRole.USER,
                    (ContentSegment.text_segment(failure_msg),),
                )
                if message is None:
                    return
                await main_memory.add_session_event(
                    session_id=main_session_id,
                    message=message,
                )
            await publish_runtime_event(
                event_type=EventType.SUBTASK_COMPLETED,
                session_id=main_session_id,
                payload={"sub_session_id": sub_session_id, "status": "failed", "immediate": True, "trigger_turn": True},
                source="subtask_failed",
            )
            state.mark_immediate_failure_notified(sub_session_id)
            logger.info(
                "Immediate sub-agent failure notified: main_session=%s, sub_session=%s",
                main_session_id,
                sub_session_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to notify immediate sub-agent failure for main_session=%s, sub_session=%s: %s",
                main_session_id,
                sub_session_id,
                exc,
            )

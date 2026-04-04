# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session memory access layer."""

from __future__ import annotations

import asyncio

from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SegmentsBody, SessionMessageBlock, ToolCallRound
from fairyclaw.core.agent.interfaces.memory_provider import CompactionSnapshot, MemoryProvider
from fairyclaw.core.domain import ContentSegment, EventType
from fairyclaw.infrastructure.database.repository import EventRepository, MemoryCompactionRepository


class PersistentMemory(MemoryProvider):
    """Repository-backed memory service for session and operation events."""

    def __init__(self, repo: EventRepository):
        """Initialize memory service with repository dependency.

        Args:
            repo (EventRepository): Event repository for persistence operations.

        Returns:
            None
        """
        self.repo = repo
        self._lock = asyncio.Lock()
        self._compaction_repo = MemoryCompactionRepository(repo.db)

    async def get_history(self, session_id: str, limit: int = 50) -> list[ChatHistoryItem]:
        """Load and parse mixed session/operation history into typed IR.

        Args:
            session_id (str): Target session identifier.
            limit (int): Maximum number of history rows to retrieve.

        Returns:
            list[ChatHistoryItem]: Parsed history IR for planner context reconstruction.
        """
        async with self._lock:
            rows = await self.repo.history(session_id=session_id, limit=limit)
        history: list[ChatHistoryItem] = []
        for r in rows:
            if r.type == EventType.SESSION_EVENT.value:
                raw_content = r.content if isinstance(r.content, list) else []
                segments: list[ContentSegment] = []
                for item in raw_content:
                    if isinstance(item, dict):
                        segments.append(ContentSegment.from_dict(item))
                entry = SessionMessageBlock.from_segments(r.role or "assistant", segments)
                if entry is not None:
                    history.append(entry)
            elif r.type == EventType.OPERATION_EVENT.value:
                history.append(
                    ToolCallRound.from_persisted(
                        event_id=r.id,
                        tool_name=r.tool_name or "",
                        tool_args=r.tool_args or {},
                        tool_result=r.tool_result,
                    )
                )
        return history

    def _serialize_message_content(self, message: SessionMessageBlock) -> list[dict[str, object]]:
        """Convert typed message block into persisted segment payload."""
        if isinstance(message.body, SegmentsBody):
            return [segment.to_dict() for segment in message.body.segments]
        return [ContentSegment.text_segment(message.as_plain_text()).to_dict()]

    async def add_session_event(self, session_id: str, message: SessionMessageBlock) -> None:
        """Persist user-visible session event.

        Args:
            session_id (str): Target session identifier.
            message (SessionMessageBlock): Typed session message block.

        Returns:
            None
        """
        async with self._lock:
            content = self._serialize_message_content(message)
            await self.repo.add_session_event(session_id=session_id, role=message.role.value, content=content)

    async def add_operation_event(self, session_id: str, tool_round: ToolCallRound) -> None:
        """Persist tool execution event for replayable operation history.

        Args:
            session_id (str): Target session identifier.
            tool_round (ToolCallRound): Typed tool round to persist.

        Returns:
            None
        """
        async with self._lock:
            await self.repo.add_operation_event(
                session_id=session_id,
                tool_name=tool_round.tool_name,
                tool_args={
                    "tool_call_id": tool_round.call_id,
                    "arguments_json": tool_round.arguments_json,
                },
                tool_result=tool_round.tool_result,
            )

    async def get_latest_compaction(self, session_id: str) -> CompactionSnapshot | None:
        """Load the latest compaction snapshot for one session."""
        async with self._lock:
            snapshot = await self._compaction_repo.latest_snapshot(session_id=session_id)
        if snapshot is None:
            return None
        return CompactionSnapshot(
            strategy=snapshot.strategy,
            summary_text=snapshot.summary_text,
            key_facts=dict(snapshot.key_facts or {}),
            from_event_id=snapshot.from_event_id,
            to_event_id=snapshot.to_event_id,
        )

    async def create_compaction_snapshot(
        self,
        session_id: str,
        strategy: str,
        summary_text: str,
        key_facts: dict[str, object] | None = None,
        from_event_id: str | None = None,
        to_event_id: str | None = None,
        created_by: str = "auto",
    ) -> CompactionSnapshot:
        """Persist one compaction snapshot for later prompt injection."""
        async with self._lock:
            snapshot = await self._compaction_repo.create_snapshot(
                session_id=session_id,
                strategy=strategy,
                summary_text=summary_text,
                key_facts=key_facts,
                from_event_id=from_event_id,
                to_event_id=to_event_id,
                created_by=created_by,
            )
        return CompactionSnapshot(
            strategy=snapshot.strategy,
            summary_text=snapshot.summary_text,
            key_facts=dict(snapshot.key_facts or {}),
            from_event_id=snapshot.from_event_id,
            to_event_id=snapshot.to_event_id,
        )

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Abstract memory provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SessionMessageBlock, ToolCallRound


@dataclass(frozen=True)
class CompactionSnapshot:
    """Structured compaction snapshot visible to hooks and planners."""

    strategy: str
    summary_text: str
    key_facts: dict[str, object]
    from_event_id: str | None = None
    to_event_id: str | None = None


class MemoryProvider(ABC):
    """Interface for short-term and long-term memory services.

    Implementation:
        Usually provided by infrastructure adapters or capability plugins.
    Injection:
        Injected into planner/tool runtime during application startup.
    Called by:
        Planner orchestration and tool executors for session persistence.
    """

    @abstractmethod
    async def get_history(self, session_id: str, limit: int = 50) -> list[ChatHistoryItem]:
        """Load typed history IR for one session."""
        raise NotImplementedError

    @abstractmethod
    async def add_session_event(self, session_id: str, message: SessionMessageBlock) -> None:
        """Persist one typed session message."""
        raise NotImplementedError

    @abstractmethod
    async def add_operation_event(self, session_id: str, tool_round: ToolCallRound) -> None:
        """Persist one typed tool round."""
        raise NotImplementedError

    async def get_latest_compaction(self, session_id: str) -> CompactionSnapshot | None:
        """Load the latest compaction snapshot when supported."""
        return None

    async def create_compaction_snapshot(
        self,
        session_id: str,
        strategy: str,
        summary_text: str,
        key_facts: dict[str, object] | None = None,
        from_event_id: str | None = None,
        to_event_id: str | None = None,
        created_by: str = "auto",
    ) -> CompactionSnapshot | None:
        """Persist a compaction snapshot when supported."""
        return None

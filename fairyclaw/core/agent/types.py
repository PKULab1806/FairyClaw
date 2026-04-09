# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Typed request and history contracts for planner pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fairyclaw.core.agent.constants import TaskType
from fairyclaw.core.agent.context.history_ir import ChatHistoryItem
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.core.domain import ContentSegment


class SessionKind(str, Enum):
    """Planner-visible session role."""

    MAIN = "main"
    SUB = "sub"


@dataclass(frozen=True)
class TurnRuntimePrefs:
    """Runtime preferences for one planner turn."""

    task_type: str = TaskType.GENERAL.value
    enabled_groups: list[str] | None = None


@dataclass(frozen=True)
class TurnRequest:
    """Typed planner turn request used by orchestration entry."""

    session_id: str
    user_segments: tuple[ContentSegment, ...]
    history_items: tuple[ChatHistoryItem, ...] = ()
    memory: PersistentMemory | None = None
    runtime: TurnRuntimePrefs = TurnRuntimePrefs()
    session_kind: SessionKind | None = None


@dataclass(frozen=True)
class SystemPromptPart:
    """Structured representation of one system prompt block."""

    text: str


@dataclass(frozen=True)
class SessionHistoryEntry:
    """Typed session event entry parsed from persistence history."""

    role: str
    content: str | tuple[ContentSegment, ...]


@dataclass(frozen=True)
class OperationHistoryEntry:
    """Typed operation event entry parsed from persistence history."""

    tool_name: str
    call_id: str
    arguments_json: str
    tool_result: str


@dataclass(frozen=True)
class UserMessageEntry:
    """Typed user message content built from user segments."""

    content: str | tuple[ContentSegment, ...]

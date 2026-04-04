# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session role policy abstractions for planner behavior differences."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fairyclaw.core.agent.constants import SUB_SESSION_MARKER


class SessionRole(str, Enum):
    """Session role kinds used by planner behavior policy."""

    MAIN = "main"
    SUB = "sub"


@dataclass(frozen=True)
class SessionRolePolicy:
    """Role policy for main/sub planner behavior decisions."""

    role: SessionRole

    @property
    def can_callback_user(self) -> bool:
        """Whether this role can directly callback user text output."""
        return self.role == SessionRole.MAIN

    @property
    def should_auto_mark_terminal_on_text(self) -> bool:
        """Whether text-only fallback should mark the subtask terminal."""
        return self.role == SessionRole.SUB


def resolve_session_role_policy(session_id: str) -> SessionRolePolicy:
    """Resolve session role policy by session identifier convention."""
    role = SessionRole.SUB if SUB_SESSION_MARKER in session_id else SessionRole.MAIN
    return SessionRolePolicy(role=role)

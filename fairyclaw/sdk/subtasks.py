# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""SDK semantic API – Category B: subtask lifecycle and global session coordination.

Thin forwarding layer over ``fairyclaw.core.agent.session.global_state``.
All business logic remains in core; this module provides a stable, named
import surface so capability scripts don't have to reach into core internals.
"""

from fairyclaw.core.agent.session.global_state import (
    SessionSubTaskState,
    SubTaskRecord,
    bind_sub_session,
    clear_sub_session_cancel,
    get_main_session_by_sub_session,
    get_or_create_subtask_state,
    get_session_lock,
    is_sub_session_cancel_requested,
    request_sub_session_cancel,
)

__all__ = [
    "SessionSubTaskState",
    "SubTaskRecord",
    "bind_sub_session",
    "clear_sub_session_cancel",
    "get_main_session_by_sub_session",
    "get_or_create_subtask_state",
    "get_session_lock",
    "is_sub_session_cancel_requested",
    "request_cancel_subtask",
]


def request_cancel_subtask(sub_session_id: str) -> None:
    """Request cancellation of a running sub-session.

    Alias for ``request_sub_session_cancel`` with a more intent-revealing name
    that matches the SDK semantic API naming convention.

    Args:
        sub_session_id: Target sub-session to cancel.
    """
    request_sub_session_cancel(sub_session_id)

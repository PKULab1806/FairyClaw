# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from typing import Any, Dict

from fairyclaw.sdk.subtasks import (
    get_or_create_subtask_state,
    request_cancel_subtask,
)
from fairyclaw.sdk.tools import ToolContext
from fairyclaw.core.events.runtime import get_user_gateway
from fairyclaw.infrastructure.database.models import SessionModel
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Cancel one running subtask by exact ID or unique prefix.

    Args:
        args (Dict[str, Any]): Tool arguments containing sub_session_id.
        context (ToolContext): Runtime context containing current main session ID.

    Returns:
        str: Human-readable cancellation result or validation error text.

    Raises:
        No exception is propagated intentionally. State-related failures are mapped to result messages.
    """
    sub_session_id = str(args.get("sub_session_id") or "").strip()
    if not sub_session_id:
        return "Error: sub_session_id is required."

    main_session_id = context.session_id
    state = get_or_create_subtask_state(main_session_id)
    resolved_task_id = state.resolve_task_id(sub_session_id)
    if not resolved_task_id:
        matches = state.find_matching_task_ids(sub_session_id)
        if len(matches) > 1:
            return f"Task ID '{sub_session_id}' is ambiguous. Matching tasks: {', '.join(sorted(matches))}"
        return f"Task {sub_session_id} is not currently running. It may have already completed, failed, or the ID is invalid."

    record = state.get_record(resolved_task_id)
    if record is None:
        return f"Task {resolved_task_id} is not currently running. It may have already completed, failed, or the ID is invalid."
    if not record.status.startswith("running"):
        return f"Task {resolved_task_id} is not currently running. Current status: {record.status}."
    request_cancel_subtask(resolved_task_id)
    state.mark_terminal(resolved_task_id, "cancelled", "Cancelled by user request.")
    try:
        async with AsyncSessionLocal() as db:
            sub_session = await db.get(SessionModel, resolved_task_id)
            if sub_session and isinstance(sub_session.meta, dict):
                meta = dict(sub_session.meta)
                meta["subtask_status"] = "cancelled"
                sub_session.meta = meta
                await db.commit()
    except Exception:
        # Best-effort persistence; runtime state still updated.
        pass
    uwg = get_user_gateway()
    if uwg is not None:
        await uwg.emit_subagent_tasks_snapshot(main_session_id)
    if context.planner is not None:
        await context.planner._publish_subtask_barrier_if_ready(resolved_task_id)
    remaining = state.active_count()
    return f"Task {resolved_task_id} has been marked as cancelled. There are {remaining} other sub-tasks still running in the background."

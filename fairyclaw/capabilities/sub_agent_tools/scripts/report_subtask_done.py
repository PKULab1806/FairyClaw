# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import logging
from typing import Any, Dict

from fairyclaw.sdk.subtasks import (
    clear_sub_session_cancel,
    get_main_session_by_sub_session,
    get_or_create_subtask_state,
)
from fairyclaw.sdk.tools import ToolContext

logger = logging.getLogger(__name__)


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Report terminal subtask status from sub-session to main-session state.

    Args:
        args (Dict[str, Any]): Status payload including status/summary/artifacts/needs_followup.
        context (ToolContext): Runtime context whose session_id is current sub-session ID.

    Returns:
        str: Human-readable reporting result text.

    Raises:
        No exception is intentionally propagated; invalid input and state errors return explicit messages.
    """
    sub_session_id = context.session_id
    main_session_id = get_main_session_by_sub_session(sub_session_id)
    if not main_session_id:
        return "Error: current session is not a registered sub-task."
    state = get_or_create_subtask_state(main_session_id)
    status = str(args.get("status") or "completed").strip().lower()
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return "Error: summary is required."
    artifacts = args.get("artifacts") or []
    artifact_lines: list[str] = []
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, str) and item.strip():
                artifact_lines.append(f"- {item.strip()}")
    needs_followup = bool(args.get("needs_followup", False))
    payload_lines = [summary]
    if artifact_lines:
        payload_lines.append("Artifacts:")
        payload_lines.extend(artifact_lines)
    payload_lines.append(f"Needs follow-up: {'yes' if needs_followup else 'no'}")
    payload = "\n".join(payload_lines)
    changed = state.mark_terminal(sub_session_id, status, payload)
    if not changed:
        return f"Sub-task {sub_session_id} already in terminal state."
    clear_sub_session_cancel(sub_session_id)
    if context.planner is not None:
        await context.planner._publish_subtask_barrier_if_ready(sub_session_id)
    logger.info(f"Sub-agent returned: main_session={main_session_id}, sub_session={sub_session_id}, status={status}")
    return f"Sub-task {sub_session_id} reported as {status}."

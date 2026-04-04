# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from typing import Any, Dict
from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.core.agent.session.global_state import get_or_create_subtask_state

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Return formatted status list for all subtasks in current main session.

    Args:
        args (Dict[str, Any]): Tool arguments (currently unused).
        context (ToolContext): Runtime context containing main session ID.

    Returns:
        str: Multi-line subtask status summary.
    """
    main_session_id = context.session_id
    state = get_or_create_subtask_state(main_session_id)
    records = state.list_records()

    if not records:
        return "No sub-tasks have been delegated in this session yet."
        
    status_lines = []
    for record in records:
        status_lines.append(
            f"- Task ID: {record.sub_session_id}\n  Instruction: {record.instruction}\n  Status: {record.status}"
        )
        
    return "Current Sub-Task Statuses:\n" + "\n\n".join(status_lines)

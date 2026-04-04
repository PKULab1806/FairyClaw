# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from typing import Any, Dict

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.core.agent.session.global_state import clear_sub_session_cancel, get_or_create_subtask_state
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.events.bus import EventType
from fairyclaw.core.events.runtime import publish_runtime_event
from fairyclaw.infrastructure.database.models import SessionModel
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal


def _normalize_task_type(value: str) -> str:
    """Normalize task type to supported profile values.

    Args:
        value (str): Raw task_type input.

    Returns:
        str: One of image/code/general, defaults to general.
    """
    normalized = value.strip().lower()
    if normalized in {"image", "code", "general"}:
        return normalized
    return "general"


def _infer_task_type_from_status(status: str) -> str:
    """Infer task type from running:<task_type> status convention.

    Args:
        status (str): Current subtask status string.

    Returns:
        str: Inferred and normalized task type.
    """
    if ":" not in status:
        return "general"
    prefix, suffix = status.split(":", 1)
    if prefix != "running":
        return "general"
    return _normalize_task_type(suffix)


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Append message to an existing subtask and optionally resume terminal task.

    Args:
        args (Dict[str, Any]): Tool arguments, including sub_session_id, message, and optional task_type.
        context (ToolContext): Runtime context containing current main session ID.

    Returns:
        str: Human-readable execution result.

    Raises:
        This function does not raise intentional business exceptions. Internal persistence failures
        are captured and converted into error result strings.
    """
    sub_session_id = str(args.get("sub_session_id") or "").strip()
    message = str(args.get("message") or "").strip()
    if not sub_session_id:
        return "Error: sub_session_id is required."
    if not message:
        return "Error: message is required."

    main_session_id = context.session_id
    state = get_or_create_subtask_state(main_session_id)
    resolved_task_id = state.resolve_task_id(sub_session_id)
    if not resolved_task_id:
        matches = state.find_matching_task_ids(sub_session_id)
        if len(matches) > 1:
            return f"Task ID '{sub_session_id}' is ambiguous. Matching tasks: {', '.join(sorted(matches))}"
        return f"Task {sub_session_id} is not found in this session."

    record = state.get_record(resolved_task_id)
    if record is None:
        return f"Task {resolved_task_id} is not found in this session."

    task_type_arg = str(args.get("task_type") or "").strip().lower()
    inferred_task_type = _infer_task_type_from_status(record.status)
    task_type = _normalize_task_type(task_type_arg) if task_type_arg else inferred_task_type
    was_terminal = state.is_terminal(resolved_task_id)
    if was_terminal:
        state.reopen_task(resolved_task_id, status=f"running:{task_type}")
    else:
        state.update_status(resolved_task_id, f"running:{task_type}")
    clear_sub_session_cancel(resolved_task_id)

    event_payload = {
        "segment_count": 1,
        "trigger_turn": True,
        "task_type": task_type,
        "parent_session_id": main_session_id,
        "resumed_from_terminal": was_terminal,
    }

    try:
        enabled_groups: list[str] | None = None
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            sub_memory = PersistentMemory(repo)
            subtask_message = SessionMessageBlock.from_segments(
                SessionMessageRole.USER,
                (ContentSegment.text_segment(message),),
            )
            if subtask_message is None:
                return "Error: Failed to construct sub-task message."
            await sub_memory.add_session_event(
                session_id=resolved_task_id,
                message=subtask_message,
            )
            sub_session = await db.get(SessionModel, resolved_task_id)
            if sub_session and isinstance(sub_session.meta, dict):
                maybe_groups = sub_session.meta.get("enabled_groups")
                if isinstance(maybe_groups, list):
                    enabled_groups = [g for g in maybe_groups if isinstance(g, str) and g.strip()]
            if enabled_groups:
                event_payload["enabled_groups"] = enabled_groups
    except Exception as e:
        return f"Error: Failed to append message to sub-session history: {e}"

    published = await publish_runtime_event(
        event_type=EventType.USER_MESSAGE_RECEIVED,
        session_id=resolved_task_id,
        payload=event_payload,
        source="message_subtask",
    )
    if not published:
        return f"Message appended but failed to enqueue sub-session event for {resolved_task_id} because runtime bus is unavailable."

    if was_terminal:
        return f"Message sent to sub-task {resolved_task_id}. Task was terminal and is now resumed as running:{task_type}."
    return f"Message sent to sub-task {resolved_task_id}. Task remains active as running:{task_type}."

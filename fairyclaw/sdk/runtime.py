# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""SDK semantic API – Category A: runtime event publishing and wakeup helpers.

Prefer the named functions over calling ``publish_runtime_event`` directly.
``publish_runtime_event`` is retained as an escape hatch for custom event types.
"""

from __future__ import annotations

from fairyclaw.core.agent.hooks.protocol import JsonObject
from fairyclaw.core.events.bus import EventType, WakeupReason
from fairyclaw.core.events.runtime import (
    deliver_file_to_user,
    publish_runtime_event,
)

__all__ = [
    "deliver_file_to_user",
    "publish_runtime_event",
    "publish_user_message_received",
    "request_planner_wakeup",
]


async def publish_user_message_received(
    session_id: str,
    *,
    task_type: str = "general",
    enabled_groups: list[str] | None = None,
    trigger_turn: bool = True,
    source: str = "capability",
) -> bool:
    """Publish a ``USER_MESSAGE_RECEIVED`` event for the given session.

    This is the preferred way for capability scripts to signal that a new user
    message should trigger a planner turn, rather than constructing the raw
    event payload by hand.

    Args:
        session_id: Target session identifier.
        task_type: LLM profile / task class for the resulting turn.
        enabled_groups: Optional capability group filter for the turn.
        trigger_turn: Whether the event should immediately start a planner turn.
        source: Source marker attached to the event for observability.

    Returns:
        True when the event was published; False when the runtime bus is unavailable.
    """
    payload: JsonObject = {
        "trigger_turn": trigger_turn,
        "task_type": task_type,
    }
    if enabled_groups is not None:
        payload["enabled_groups"] = enabled_groups  # type: ignore[assignment]
    return await publish_runtime_event(
        EventType.USER_MESSAGE_RECEIVED,
        session_id,
        payload=payload,
        source=source,
    )


async def request_planner_wakeup(
    session_id: str,
    *,
    reason: str = WakeupReason.SYSTEM.value,
    source: str = "capability",
) -> bool:
    """Publish a ``WAKEUP_REQUESTED`` event to explicitly resume the planner.

    Use this when a capability needs to nudge the planner back into action
    without delivering a full user message (e.g. after an async background
    operation completes independently of a subtask).

    Args:
        session_id: Target session identifier.
        reason: Wakeup reason string; defaults to ``WakeupReason.SYSTEM``.
        source: Source marker attached to the event for observability.

    Returns:
        True when the event was published; False when the runtime bus is unavailable.
    """
    payload: JsonObject = {"reason": reason}
    return await publish_runtime_event(
        EventType.WAKEUP_REQUESTED,
        session_id,
        payload=payload,
        source=source,
    )

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime event bus access module.

Provides global runtime bus registration and publishing entrypoints,
decoupling event flow among API, planner, and tool scripts.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from typing import TYPE_CHECKING

from fairyclaw.core.agent.hooks.protocol import JsonObject
from fairyclaw.core.events.bus import RuntimeEvent, RuntimeEventType, SessionEventBus, event_type_value

if TYPE_CHECKING:
    from fairyclaw.bridge.user_gateway import UserGateway

logger = logging.getLogger(__name__)
_runtime_bus: SessionEventBus | None = None
_file_delivery: Callable[[str, str], Awaitable[None]] | None = None
_user_gateway: UserGateway | None = None


def set_runtime_bus(bus: SessionEventBus | None) -> None:
    """Register or clear the global runtime event bus singleton.

    Args:
        bus: Active bus, or None at shutdown.

    Returns:
        None
    """
    global _runtime_bus
    _runtime_bus = bus


def get_runtime_bus() -> SessionEventBus | None:
    """Get current global runtime event bus singleton.

    Returns:
        SessionEventBus | None: Registered bus instance or None.
    """
    return _runtime_bus


def set_user_gateway(gw: UserGateway | None) -> None:
    """Register the business-side UserGateway (bridge + user channel)."""
    global _user_gateway
    _user_gateway = gw


def get_user_gateway() -> UserGateway | None:
    """Return the registered UserGateway, if any."""
    return _user_gateway


def set_file_delivery(fn: Callable[[str, str], Awaitable[None]] | None) -> None:
    """Register global file delivery callable used by send_file."""
    global _file_delivery
    _file_delivery = fn


async def deliver_file_to_user(session_id: str, file_id: str) -> None:
    """Deliver one stored session file to the active user channel."""
    if _file_delivery is None:
        logger.warning(
            "deliver_file_to_user: no delivery registered, session=%s file_id=%s",
            session_id,
            file_id,
        )
        return
    await _file_delivery(session_id, file_id)


async def publish_runtime_event(
    event_type: RuntimeEventType,
    session_id: str,
    payload: JsonObject | None = None,
    source: str = "runtime",
) -> bool:
    """Publish one runtime event through global event bus.

    Args:
        event_type (RuntimeEventType): Event category.
        session_id (str): Target session identifier.
        payload (JsonObject | None): Optional event payload.
        source (str): Event source marker.

    Returns:
        bool: True when event is published; False when runtime bus is unavailable.
    """
    bus = get_runtime_bus()
    if bus is None:
        logger.warning(f"Runtime bus not initialized. event={event_type_value(event_type)} session={session_id}")
        return False
    event = RuntimeEvent(
        type=event_type,
        session_id=session_id,
        payload=payload or {},
        source=source,
    )
    await bus.publish(event)
    return True

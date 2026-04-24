# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Bootstrap / teardown the Business runtime (planner, bus, scheduler) without HTTP."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from fairyclaw.bridge.gateway_control import BusinessGatewayControl
from fairyclaw.bridge.user_gateway import UserGateway
from fairyclaw.config.settings import settings
from fairyclaw.core.agent.planning.planner import Planner
from fairyclaw.core.events.bus import SessionEventBus
from fairyclaw.core.events.plugin_dispatcher import EventPluginDispatcher
from fairyclaw.core.events.runtime import (
    get_user_gateway,
    set_file_delivery,
    set_runtime_bus,
    set_user_gateway,
)
from fairyclaw.core.events.session_scheduler import RuntimeSessionScheduler
from fairyclaw.core.gateway_protocol.control_envelope import HeartbeatInfo, TelemetrySnapshot
from fairyclaw.core.gateway_protocol.models import now_ms
from fairyclaw.infrastructure.database.models import Base
from fairyclaw.infrastructure.database.session import engine
from fairyclaw.infrastructure.logging_setup import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class BusinessRuntime:
    """Holds the in-process session engine (same as ``fairyclaw.main`` ASGI app)."""

    planner: Planner
    bus: SessionEventBus
    scheduler: RuntimeSessionScheduler
    user_gateway: UserGateway
    gw_control: BusinessGatewayControl
    event_dispatcher: EventPluginDispatcher
    telemetry_task: asyncio.Task


async def startup_business_runtime() -> BusinessRuntime:
    """Initialize database, bus, scheduler, and gateway helpers (idempotent for one process)."""
    setup_logging()
    settings.ensure_dirs()
    if settings.filesystem_root_dir:
        try:
            os.chdir(settings.filesystem_root_dir)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Could not chdir to filesystem_root_dir: %s", e)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    planner = Planner()
    event_dispatcher = EventPluginDispatcher(planner.registry)
    bus = SessionEventBus(worker_count=settings.event_bus_worker_count)
    gw_control = BusinessGatewayControl(planner)
    user_gateway = UserGateway(bus=bus, gateway_control=gw_control)
    set_user_gateway(user_gateway)

    async def telemetry_loop() -> None:
        while True:
            await asyncio.sleep(30)
            uwg = get_user_gateway()
            if uwg is None:
                continue
            snap = TelemetrySnapshot(
                heartbeat=HeartbeatInfo(status="HEARTBEAT_OK", server_time_ms=now_ms(), message=None),
                reins_enabled=settings.reins_enabled,
            )
            await uwg.emit_telemetry_snapshot(snap)

    telemetry_task = asyncio.create_task(telemetry_loop(), name="fairyclaw-telemetry")
    set_file_delivery(user_gateway.emit_file)
    scheduler = RuntimeSessionScheduler(
        bus=bus,
        planner=planner,
        event_dispatcher=event_dispatcher,
    )
    await scheduler.start()
    set_runtime_bus(bus)
    return BusinessRuntime(
        planner=planner,
        bus=bus,
        scheduler=scheduler,
        user_gateway=user_gateway,
        gw_control=gw_control,
        event_dispatcher=event_dispatcher,
        telemetry_task=telemetry_task,
    )


async def shutdown_business_runtime(rt: BusinessRuntime) -> None:
    """Stop scheduler, bus, and clear globals; cancel telemetry task."""
    if not rt.telemetry_task.done():
        rt.telemetry_task.cancel()
        try:
            await rt.telemetry_task
        except asyncio.CancelledError:
            pass
    await rt.scheduler.stop()
    await rt.bus.stop()
    set_file_delivery(None)
    set_user_gateway(None)
    set_runtime_bus(None)
    logger.info("FairyClaw in-process runtime shutdown complete")

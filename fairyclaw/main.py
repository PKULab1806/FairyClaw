# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""FairyClaw application entrypoint.

Initialize web API, database, event bus, session scheduler, and watchdog.
"""

import asyncio
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from fairyclaw.bridge.gateway_control import BusinessGatewayControl
from fairyclaw.bridge.user_gateway import UserGateway, create_ws_bridge_router
from fairyclaw.config.settings import settings
from fairyclaw.core.agent.planning.planner import Planner
from fairyclaw.core.gateway_protocol.control_envelope import HeartbeatInfo, TelemetrySnapshot
from fairyclaw.core.gateway_protocol.models import now_ms
from fairyclaw.core.events.bus import SessionEventBus
from fairyclaw.core.events.plugin_dispatcher import EventPluginDispatcher
from fairyclaw.core.events.runtime import get_user_gateway, set_file_delivery, set_runtime_bus, set_user_gateway
from fairyclaw.core.events.session_scheduler import RuntimeSessionScheduler
from fairyclaw.infrastructure.database.models import Base
from fairyclaw.infrastructure.database.session import engine
from fairyclaw.infrastructure.logging_setup import setup_logging

logger = logging.getLogger(__name__)

app = FastAPI(title="FairyClaw", version="0.1.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup() -> None:
    """Initialize application runtime components.

    Returns:
        None

    Raises:
        Startup exceptions propagate to ASGI server and fail application boot.
    """
    setup_logging()
    settings.ensure_dirs()
    if settings.filesystem_root_dir:
        import os
        try:
            os.chdir(settings.filesystem_root_dir)
            print(f"Working directory changed to: {settings.filesystem_root_dir}")
        except FileNotFoundError:
            print(f"Warning: filesystem_root_dir '{settings.filesystem_root_dir}' not found. Using default CWD.")
        except Exception as e:
            print(f"Error changing working directory: {e}")

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

    asyncio.create_task(telemetry_loop())
    set_file_delivery(user_gateway.emit_file)
    scheduler = RuntimeSessionScheduler(
        bus=bus,
        planner=planner,
        event_dispatcher=event_dispatcher,
    )
    await scheduler.start()
    set_runtime_bus(bus)
    app.state.runtime_bus = bus
    app.state.runtime_planner = planner
    app.state.runtime_scheduler = scheduler
    app.state.user_gateway = user_gateway
    app.include_router(create_ws_bridge_router(user_gateway), tags=["bridge"])


@app.on_event("shutdown")
async def shutdown() -> None:
    """Shutdown runtime bus and background watchdog task.

    Returns:
        None
    """
    scheduler = getattr(app.state, "runtime_scheduler", None)
    if isinstance(scheduler, RuntimeSessionScheduler):
        await scheduler.stop()
    bus = getattr(app.state, "runtime_bus", None)
    if bus:
        await bus.stop()
    set_file_delivery(None)
    set_user_gateway(None)
    logger.info("FairyClaw shutdown complete")


@app.get("/healthz")
async def healthz():
    """Return health check payload.

    Returns:
        dict[str, str]: Static service status response.
    """
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    """Convert HTTPException into unified error response schema.

    Args:
        _ (Request): Incoming request object (unused).
        exc (HTTPException): Raised FastAPI HTTP exception.

    Returns:
        JSONResponse: Standardized error payload.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": str(exc.detail),
                "type": "invalid_request_error",
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError):
    """Convert request validation errors into unified schema.

    Args:
        _ (Request): Incoming request object (unused).
        exc (RequestValidationError): Validation exception from FastAPI.

    Returns:
        JSONResponse: Standardized validation error payload.
    """
    first = exc.errors()[0] if exc.errors() else {"msg": "Validation failed", "loc": []}
    param = first.get("loc", [])
    param_name = param[-1] if param else None
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": first.get("msg", "Validation failed"),
                "param": param_name,
                "type": "invalid_request_error",
            }
        },
    )


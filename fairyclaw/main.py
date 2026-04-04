# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""FairyClaw application entrypoint.

Initialize web API, database, event bus, session scheduler, and watchdog.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from fairyclaw.bridge.ws_server import WsBridgeServer, create_ws_bridge_router
from fairyclaw.core.agent.planning.planner import Planner
from fairyclaw.core.events.bus import SessionEventBus
from fairyclaw.core.events.plugin_dispatcher import EventPluginDispatcher
from fairyclaw.core.events.runtime import set_file_delivery
from fairyclaw.core.events.session_scheduler import RuntimeSessionScheduler
from fairyclaw.core.events.runtime import set_runtime_bus
from fairyclaw.config.settings import settings
from fairyclaw.infrastructure.database.models import Base
from fairyclaw.infrastructure.database.session import engine
from fairyclaw.infrastructure.logging_setup import setup_logging

logger = logging.getLogger(__name__)

app = FastAPI(title="FairyClaw", version="0.1.0")
bridge_server = WsBridgeServer()

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
    set_file_delivery(bridge_server.deliver_file_to_user)
    scheduler = RuntimeSessionScheduler(
        bus=bus,
        planner=planner,
        event_dispatcher=event_dispatcher,
        push_outbound=bridge_server.push_outbound,
    )
    await scheduler.start()
    set_runtime_bus(bus)
    app.state.runtime_bus = bus
    app.state.runtime_planner = planner
    app.state.runtime_scheduler = scheduler
    app.state.bridge_server = bridge_server


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

app.include_router(create_ws_bridge_router(bridge_server), tags=["bridge"])

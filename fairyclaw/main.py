# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""FairyClaw application entrypoint.

Initialize web API, database, event bus, session scheduler, and watchdog.
"""

import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from fairyclaw.bridge.user_gateway import create_ws_bridge_router
from fairyclaw.config.settings import settings
from fairyclaw.runtime.lifecycle import BusinessRuntime, shutdown_business_runtime, startup_business_runtime

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
    if settings.filesystem_root_dir:
        try:
            os.chdir(settings.filesystem_root_dir)
            print(f"Working directory changed to: {settings.filesystem_root_dir}")
        except FileNotFoundError:
            print(f"Warning: filesystem_root_dir '{settings.filesystem_root_dir}' not found. Using default CWD.")
        except OSError as e:
            print(f"Error changing working directory: {e}")

    rt = await startup_business_runtime()
    app.state.business_runtime = rt
    app.state.runtime_bus = rt.bus
    app.state.runtime_planner = rt.planner
    app.state.runtime_scheduler = rt.scheduler
    app.state.user_gateway = rt.user_gateway
    app.include_router(create_ws_bridge_router(rt.user_gateway), tags=["bridge"])


@app.on_event("shutdown")
async def shutdown() -> None:
    """Shutdown runtime bus and background watchdog task.

    Returns:
        None
    """
    logger.info("FairyClaw Business ASGI shutdown starting")
    rt: BusinessRuntime | None = getattr(app.state, "business_runtime", None)
    if rt is not None:
        await shutdown_business_runtime(rt)
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


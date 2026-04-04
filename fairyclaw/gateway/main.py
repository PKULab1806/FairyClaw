# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Gateway process entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from fairyclaw.config.settings import settings
from fairyclaw.gateway.adapters.http_adapter import HttpGatewayAdapter
from fairyclaw.gateway.adapters.onebot_adapter import OneBotGatewayAdapter
from fairyclaw.gateway.runtime import GatewayRuntime
from fairyclaw.infrastructure.database.models import Base
from fairyclaw.infrastructure.database.session import engine

app = FastAPI(title="FairyClaw Gateway", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

runtime = GatewayRuntime(adapters=[HttpGatewayAdapter(), OneBotGatewayAdapter()])
app.include_router(runtime.build_router())


def _mount_web_app() -> bool:
    """Mount built SPA assets when web/dist exists."""
    dist_dir = Path(__file__).resolve().parents[2] / "web" / "dist"
    if not dist_dir.exists():
        return False
    app.mount("/app", StaticFiles(directory=str(dist_dir), html=True), name="web-app")
    return True


HAS_WEB_APP = _mount_web_app()


@app.on_event("startup")
async def startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await runtime.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await runtime.stop()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_model=None)
async def root():
    if HAS_WEB_APP:
        return RedirectResponse(url="/app")
    return {"status": "ok", "message": "FairyClaw Gateway is running"}

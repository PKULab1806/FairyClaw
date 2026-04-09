# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Gateway process entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from fairyclaw.config.settings import settings
from fairyclaw.gateway.adapters.web_gateway_adapter import WebGatewayAdapter
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

runtime = GatewayRuntime(adapters=[WebGatewayAdapter(), OneBotGatewayAdapter()])
app.include_router(runtime.build_router())

WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
HAS_WEB_APP = WEB_DIST_DIR.exists()


def _web_file(path: str = "") -> Path:
    candidate = (WEB_DIST_DIR / path).resolve()
    candidate.relative_to(WEB_DIST_DIR.resolve())
    return candidate


if HAS_WEB_APP:
    @app.get("/app", include_in_schema=False)
    async def web_app_root() -> RedirectResponse:
        return RedirectResponse(url="/app/")


    @app.get("/app/", include_in_schema=False)
    async def web_app_index() -> FileResponse:
        return FileResponse(WEB_DIST_DIR / "index.html", headers={"Cache-Control": "no-store"})


    @app.get("/app/{path:path}", include_in_schema=False)
    async def web_app_path(path: str) -> FileResponse:
        try:
            candidate = _web_file(path)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc

        if candidate.is_file():
            headers = {"Cache-Control": "no-store"} if candidate.suffix == ".html" else None
            return FileResponse(candidate, headers=headers)

        return FileResponse(WEB_DIST_DIR / "index.html", headers={"Cache-Control": "no-store"})


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

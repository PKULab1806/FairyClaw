# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Web UI gateway adapter: browser connects only via WebSocket at ``/v1/ws`` (no REST chat API)."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque

from fastapi import APIRouter, Query, WebSocket

from fairyclaw.config.settings import settings
from fairyclaw.core.gateway_protocol.models import (
    GatewayOutboundMessage,
    OUTBOUND_BROADCAST_SESSION_ID,
    now_ms,
)
from fairyclaw.gateway.adapters.base import GatewayAdapter
from fairyclaw.gateway.adapters.web_gateway_ws import MSG_PUSH, run_web_gateway_socket


class WebGatewayAdapter(GatewayAdapter):
    """Expose the SPA via a single WebSocket at ``/v1/ws``; push assistant output to bound sessions."""

    # Persisted session routes still use this key (historical name); transport is WebSocket-only.
    adapter_key = "http"
    kind = "web_ws"

    def __init__(self) -> None:
        self._session_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._backlog: dict[str, deque[dict[str, object]]] = defaultdict(lambda: deque(maxlen=128))
        self._all_websockets: set[WebSocket] = set()
        self._broadcast_backlog: deque[dict[str, object]] = deque(maxlen=64)
        self._lock = asyncio.Lock()

    def build_router(self) -> APIRouter:
        router = APIRouter()

        @router.websocket("/v1/ws")
        async def web_gateway_websocket(
            websocket: WebSocket,
            token: str | None = Query(None),
        ) -> None:
            if not token or token != settings.api_token:
                await websocket.close(code=1008)
                return
            await websocket.accept()
            await run_web_gateway_socket(self, websocket)

        return router

    async def send(self, outbound: GatewayOutboundMessage) -> None:
        """Push outbound payload to session WebSocket subscribers."""
        if outbound.kind not in {"text", "file", "event"}:
            raise RuntimeError(f"Unsupported outbound kind for web gateway adapter: {outbound.kind}")
        if outbound.session_id == OUTBOUND_BROADCAST_SESSION_ID and outbound.kind != "event":
            raise RuntimeError("Broadcast session id only supports kind=event")
        content: dict[str, object] = dict(outbound.content)
        if outbound.kind == "file":
            file_id = content.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise RuntimeError("Missing file_id in outbound file payload")
            file_bytes, filename, mime_type = await self.runtime.download_file(
                session_id=outbound.session_id,
                file_id=file_id,
            )
            content = {
                "file_id": file_id,
                "filename": filename or file_id,
                "mime_type": mime_type,
                "size_bytes": len(file_bytes),
            }
        payload_body: dict[str, object] = {
            "session_id": outbound.session_id,
            "kind": outbound.kind,
            "content": content,
            "meta": dict(outbound.meta),
        }
        if outbound.adapter_key is not None:
            payload_body["adapter_key"] = outbound.adapter_key
        if outbound.sender_ref is not None:
            payload_body["sender_ref"] = dict(outbound.sender_ref)
        envelope = {"op": MSG_PUSH, "body": payload_body}
        async with self._lock:
            if outbound.session_id == OUTBOUND_BROADCAST_SESSION_ID:
                self._broadcast_backlog.append(envelope)
                targets = list(self._all_websockets)
            else:
                sockets = list(self._session_sockets.get(outbound.session_id, ()))
                self._backlog[outbound.session_id].append(envelope)
                targets = sockets
        for websocket in targets:
            await websocket.send_json(envelope)

    async def register_client(self, websocket: WebSocket) -> None:
        """Track one web client for broadcast pushes; replay recent broadcast backlog."""
        async with self._lock:
            self._all_websockets.add(websocket)
            backlog = list(self._broadcast_backlog)
        for item in backlog:
            await websocket.send_json(item)

    async def unregister_client(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._all_websockets.discard(websocket)

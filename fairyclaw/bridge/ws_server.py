# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Business-side WebSocket bridge server."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import Iterable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from fairyclaw.config.settings import settings
from fairyclaw.core.agent.session.session_role import resolve_session_role_policy
from fairyclaw.core.gateway_protocol.files import GatewayFileService
from fairyclaw.core.gateway_protocol.ingress import GatewayIngressService
from fairyclaw.core.gateway_protocol.models import (
    ACK_STATUS_DUPLICATE,
    ACK_STATUS_FAILED,
    ACK_STATUS_INVALID,
    ACK_STATUS_OK,
    FRAME_ACK,
    FRAME_ERROR,
    FRAME_FILE_GET,
    FRAME_FILE_GET_ACK,
    FRAME_FILE_GET_CHUNK,
    FRAME_FILE_PUT_CHUNK,
    FRAME_FILE_PUT_COMMIT,
    FRAME_FILE_PUT_INIT,
    FRAME_HEARTBEAT,
    FRAME_HELLO,
    FRAME_HELLO_ACK,
    FRAME_INBOUND,
    FRAME_OUTBOUND,
    FRAME_RESUME,
    FRAME_SESSION_OPEN,
    FRAME_SESSION_OPEN_ACK,
    AckPayload,
    BridgeFrame,
    ErrorPayload,
    GatewayFileGetRequest,
    GatewayFilePutCommit,
    GatewayFilePutInit,
    GatewayInboundMessage,
    GatewayOutboundMessage,
    HelloAckPayload,
    SessionOpenAckPayload,
    SessionOpenPayload,
    new_frame_id,
)
from fairyclaw.infrastructure.database.repository import FileRepository, GatewaySessionRouteRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class _RecentFrameSet:
    """Bounded LRU-like frame id deduper."""

    def __init__(self, limit: int = 2048) -> None:
        self.limit = max(1, limit)
        self._data: OrderedDict[str, None] = OrderedDict()

    def add(self, frame_id: str) -> None:
        self._data.pop(frame_id, None)
        self._data[frame_id] = None
        while len(self._data) > self.limit:
            self._data.popitem(last=False)

    def __contains__(self, frame_id: str) -> bool:
        return frame_id in self._data


class WsBridgeServer:
    """Own the business-side WebSocket bridge connection state."""

    def __init__(self) -> None:
        self.ingress = GatewayIngressService()
        self.files = GatewayFileService()
        self._active_websocket: WebSocket | None = None
        self._active_gateway_id: str | None = None
        self._connection_id: str | None = None
        self._state_lock = asyncio.Lock()
        self._processed_inbound = _RecentFrameSet()
        self._acked_outbound: set[str] = set()
        self._outbound_backlog: deque[BridgeFrame] = deque(maxlen=settings.bridge_outbound_backlog_size)

    async def push_outbound(self, message) -> None:
        """Queue and send one outbound business message."""
        frame = BridgeFrame(type=FRAME_OUTBOUND, payload=message.to_payload(), id=new_frame_id("out"))
        async with self._state_lock:
            self._outbound_backlog.append(frame)
            websocket = self._active_websocket
        if websocket is not None:
            await websocket.send_text(frame.to_json())

    async def deliver_file_to_user(self, session_id: str, file_id: str) -> None:
        """Deliver one stored file to the user channel, remapping sub-sessions to parents."""
        target_session_id = session_id
        target_file_id = file_id

        if not resolve_session_role_policy(session_id).can_callback_user:
            async with AsyncSessionLocal() as db:
                route_repo = GatewaySessionRouteRepository(db)
                parent_session_id = await route_repo.get_parent_session_id(session_id)
            if not parent_session_id:
                logger.warning("deliver_file_to_user: no parent route for sub-session=%s", session_id)
                return
            async with AsyncSessionLocal() as db:
                file_repo = FileRepository(db)
                cloned = await file_repo.clone_to_session(
                    file_id=file_id,
                    source_session_id=session_id,
                    target_session_id=parent_session_id,
                )
            if cloned is None:
                logger.error(
                    "deliver_file_to_user: clone failed, source=%s target=%s file_id=%s",
                    session_id,
                    parent_session_id,
                    file_id,
                )
                return
            target_session_id = parent_session_id
            target_file_id = cloned.id

        if not resolve_session_role_policy(target_session_id).can_callback_user:
            logger.warning("deliver_file_to_user: target session not deliverable: %s", target_session_id)
            return

        await self.push_outbound(
            GatewayOutboundMessage.file(
                session_id=target_session_id,
                file_id=target_file_id,
            )
        )

    async def _send_frame(self, websocket: WebSocket, frame: BridgeFrame) -> None:
        await websocket.send_text(frame.to_json())

    async def _send_ack(
        self,
        websocket: WebSocket,
        *,
        ref_type: str,
        ref_id: str,
        status: str,
        error: dict | None = None,
    ) -> None:
        await self._send_frame(
            websocket,
            BridgeFrame(
                type=FRAME_ACK,
                payload=AckPayload(ref_type=ref_type, ref_id=ref_id, status=status, error=error).to_dict(),
            ),
        )

    async def _send_error(self, websocket: WebSocket, *, code: str, message: str, details: dict | None = None) -> None:
        await self._send_frame(
            websocket,
            BridgeFrame(type=FRAME_ERROR, payload=ErrorPayload(code=code, message=message, details=details).to_dict()),
        )

    async def _replay_backlog(self, websocket: WebSocket) -> None:
        frames: Iterable[BridgeFrame]
        async with self._state_lock:
            frames = list(self._outbound_backlog)
            acked = set(self._acked_outbound)
        for frame in frames:
            if frame.id in acked:
                continue
            await websocket.send_text(frame.to_json())

    async def _handle_frame(self, websocket: WebSocket, frame: BridgeFrame) -> None:
        frame_type = frame.type
        if frame_type == FRAME_HELLO:
            payload = frame.payload
            token = payload.get("token")
            if token != settings.bridge_token:
                await self._send_frame(
                    websocket,
                    BridgeFrame(
                        type=FRAME_HELLO_ACK,
                        payload=HelloAckPayload(
                            ok=False,
                            connection_id="",
                            error={"code": "auth_failed", "message": "Invalid bridge token"},
                        ).to_dict(),
                    ),
                )
                await websocket.close()
                return
            gateway_id = str(payload.get("gateway_id") or "")
            connection_id = new_frame_id("conn")
            async with self._state_lock:
                self._active_websocket = websocket
                self._active_gateway_id = gateway_id
                self._connection_id = connection_id
            await self._send_frame(
                websocket,
                BridgeFrame(
                    type=FRAME_HELLO_ACK,
                    payload=HelloAckPayload(
                        ok=True,
                        connection_id=connection_id,
                        limits={
                            "max_file_bytes": settings.bridge_max_file_bytes,
                            "max_chunk_bytes": settings.bridge_max_chunk_bytes,
                            "max_inflight_file_transfers": settings.bridge_max_inflight_file_transfers,
                        },
                    ).to_dict(),
                ),
            )
            return

        if frame_type == FRAME_RESUME:
            await self._replay_backlog(websocket)
            return

        if frame_type == FRAME_SESSION_OPEN:
            payload = SessionOpenPayload(
                adapter_key=str(frame.payload.get("adapter_key") or ""),
                platform=str(frame.payload.get("platform") or "gateway"),
                title=str(frame.payload.get("title")) if frame.payload.get("title") is not None else None,
                meta=frame.payload.get("meta") if isinstance(frame.payload.get("meta"), dict) else {},
                session_id=str(frame.payload.get("session_id")) if frame.payload.get("session_id") is not None else None,
            )
            try:
                session_id = await self.ingress.open_session(
                    platform=payload.platform,
                    title=payload.title,
                    meta=payload.meta,
                )
                await self._send_frame(
                    websocket,
                    BridgeFrame(
                        type=FRAME_SESSION_OPEN_ACK,
                        payload=SessionOpenAckPayload(ok=True, session_id=session_id).to_dict(),
                        id=frame.id,
                    ),
                )
            except Exception as exc:
                await self._send_frame(
                    websocket,
                    BridgeFrame(
                        type=FRAME_SESSION_OPEN_ACK,
                        payload=SessionOpenAckPayload(
                            ok=False,
                            session_id=None,
                            error={"code": "session_open_failed", "message": str(exc)},
                        ).to_dict(),
                        id=frame.id,
                    ),
                )
            return

        if frame_type == FRAME_INBOUND:
            if frame.id in self._processed_inbound:
                await self._send_ack(websocket, ref_type="inbound", ref_id=frame.id, status=ACK_STATUS_DUPLICATE)
                return
            try:
                message = GatewayInboundMessage.from_payload(frame.payload)
                await self.ingress.submit_message(message)
                self._processed_inbound.add(frame.id)
                await self._send_ack(websocket, ref_type="inbound", ref_id=frame.id, status=ACK_STATUS_OK)
            except Exception as exc:
                await self._send_ack(
                    websocket,
                    ref_type="inbound",
                    ref_id=frame.id,
                    status=ACK_STATUS_FAILED,
                    error={"code": "inbound_failed", "message": str(exc)},
                )
            return

        if frame_type == FRAME_FILE_PUT_INIT:
            request = GatewayFilePutInit(
                session_id=str(frame.payload.get("session_id") or ""),
                adapter_key=str(frame.payload.get("adapter_key") or ""),
                message_id=str(frame.payload.get("message_id") or ""),
                filename=str(frame.payload.get("filename") or ""),
                mime_type=str(frame.payload.get("mime_type")) if frame.payload.get("mime_type") is not None else None,
                size_bytes=int(frame.payload.get("size_bytes") or 0),
                sha256_hex=str(frame.payload.get("sha256_hex") or ""),
            )
            ack = await self.files.put_init(request)
            await self._send_frame(websocket, BridgeFrame(type="file_put_ack", payload=ack.to_payload(), id=frame.id))
            return

        if frame_type == FRAME_FILE_PUT_CHUNK:
            ack = await self.files.put_chunk(
                upload_id=str(frame.payload.get("upload_id") or ""),
                seq=int(frame.payload.get("seq") or 0),
                data_b64=str(frame.payload.get("data_b64") or ""),
                chunk_bytes=int(frame.payload.get("chunk_bytes") or 0),
            )
            await self._send_frame(websocket, BridgeFrame(type="file_put_ack", payload=ack.to_payload(), id=frame.id))
            return

        if frame_type == FRAME_FILE_PUT_COMMIT:
            ack = await self.files.put_commit(
                GatewayFilePutCommit(
                    upload_id=str(frame.payload.get("upload_id") or ""),
                    total_chunks=int(frame.payload.get("total_chunks") or 0),
                )
            )
            await self._send_frame(websocket, BridgeFrame(type="file_put_ack", payload=ack.to_payload(), id=frame.id))
            return

        if frame_type == FRAME_FILE_GET:
            chunks, ack = await self.files.get_chunks(
                GatewayFileGetRequest(
                    session_id=str(frame.payload.get("session_id") or ""),
                    file_id=str(frame.payload.get("file_id") or ""),
                    request_id=str(frame.payload.get("request_id") or frame.id),
                ),
                chunk_size=settings.bridge_max_chunk_bytes,
            )
            for chunk in chunks:
                await self._send_frame(
                    websocket,
                    BridgeFrame(type=FRAME_FILE_GET_CHUNK, payload=chunk.to_payload()),
                )
            await self._send_frame(
                websocket,
                BridgeFrame(type=FRAME_FILE_GET_ACK, payload=ack.to_payload(), id=frame.id),
            )
            return

        if frame_type == FRAME_ACK:
            ref_type = str(frame.payload.get("ref_type") or "")
            ref_id = str(frame.payload.get("ref_id") or "")
            status = str(frame.payload.get("status") or "")
            if ref_type == "outbound" and status in {ACK_STATUS_OK, ACK_STATUS_DUPLICATE}:
                async with self._state_lock:
                    self._acked_outbound.add(ref_id)
            return

        if frame_type == FRAME_HEARTBEAT:
            await self._send_frame(websocket, BridgeFrame(type=FRAME_HEARTBEAT, payload=frame.payload))
            return

        await self._send_error(
            websocket,
            code="invalid_frame",
            message=f"Unsupported frame type: {frame.type}",
        )
        await self._send_ack(
            websocket,
            ref_type="frame",
            ref_id=frame.id,
            status=ACK_STATUS_INVALID,
            error={"code": "invalid_frame", "message": f"Unsupported frame type: {frame.type}"},
        )

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Accept and process one gateway WebSocket connection."""
        await websocket.accept()
        try:
            while True:
                raw = await websocket.receive_text()
                frame = BridgeFrame.from_json(raw)
                await self._handle_frame(websocket, frame)
        except WebSocketDisconnect:
            logger.info("Gateway bridge disconnected")
        finally:
            async with self._state_lock:
                if self._active_websocket is websocket:
                    self._active_websocket = None
                    self._active_gateway_id = None
                    self._connection_id = None


def create_ws_bridge_router(server: WsBridgeServer) -> APIRouter:
    """Build FastAPI router for bridge websocket endpoint."""
    router = APIRouter()

    @router.websocket(settings.bridge_ws_path)
    async def gateway_ws(websocket: WebSocket) -> None:
        await server.handle_connection(websocket)

    return router

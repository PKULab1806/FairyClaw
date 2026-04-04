# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Gateway-side WebSocket bridge client."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import websockets

from fairyclaw.config.settings import settings
from fairyclaw.core.gateway_protocol.models import (
    ACK_STATUS_DUPLICATE,
    ACK_STATUS_OK,
    FRAME_ACK,
    FRAME_FILE_GET,
    FRAME_FILE_GET_ACK,
    FRAME_FILE_GET_CHUNK,
    FRAME_FILE_PUT_ACK,
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
    GatewayFileGetRequest,
    GatewayFilePutChunk,
    GatewayFilePutCommit,
    GatewayFilePutInit,
    GatewayInboundMessage,
    GatewayOutboundMessage,
    HelloPayload,
    ResumePayload,
    SessionOpenPayload,
    new_frame_id,
    sha256_hex,
)

logger = logging.getLogger(__name__)


@dataclass
class _DownloadState:
    """Collect chunks for one file download request."""

    request_id: str
    file_id: str
    chunks: dict[int, bytes] = field(default_factory=dict)
    filename: str | None = None
    mime_type: str | None = None
    ack_future: asyncio.Future[BridgeFrame] | None = None


class WsBridgeClient:
    """Maintain one persistent connection to the business bridge server."""

    def __init__(self, *, runtime: "GatewayRuntime") -> None:
        self.runtime = runtime
        self._ws: Any | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._ack_waiters: dict[str, asyncio.Future[BridgeFrame]] = {}
        self._session_waiters: dict[str, asyncio.Future[BridgeFrame]] = {}
        self._file_put_waiters: dict[str, asyncio.Future[BridgeFrame]] = {}
        self._downloads: dict[str, _DownloadState] = {}
        self._pending_inbound_frames: "OrderedDict[str, BridgeFrame]" = OrderedDict()
        self._last_ack_inbound_id: str | None = None
        self._last_ack_outbound_id: str | None = None
        self._limits: dict[str, Any] = {
            "max_file_bytes": settings.bridge_max_file_bytes,
            "max_chunk_bytes": settings.bridge_max_chunk_bytes,
        }

    async def _send_frame(self, frame: BridgeFrame) -> None:
        async with self._send_lock:
            if self._ws is None:
                raise RuntimeError("Bridge websocket is not connected")
            await self._ws.send(frame.to_json())

    async def _handshake(self) -> None:
        adapters = tuple(adapter.descriptor() for adapter in self.runtime.adapters.values())
        hello = BridgeFrame(
            type=FRAME_HELLO,
            payload=HelloPayload(
                gateway_id=settings.gateway_id,
                token=settings.bridge_token,
                adapters=adapters,
                supports={"resume": True},
            ).to_dict(),
            id=new_frame_id("hello"),
        )
        await self._send_frame(hello)
        raw = await self._ws.recv()
        frame = BridgeFrame.from_json(raw)
        if frame.type != FRAME_HELLO_ACK:
            raise RuntimeError(f"Unexpected handshake frame: {frame.type}")
        if not bool(frame.payload.get("ok")):
            error = frame.payload.get("error") if isinstance(frame.payload.get("error"), dict) else {}
            raise RuntimeError(str(error.get("message") or "Bridge hello rejected"))
        limits = frame.payload.get("limits")
        if isinstance(limits, dict):
            self._limits.update(limits)
        self._ready_event.set()
        resume = BridgeFrame(
            type=FRAME_RESUME,
            payload=ResumePayload(
                gateway_id=settings.gateway_id,
                last_ack_inbound_id=self._last_ack_inbound_id,
                last_ack_outbound_id=self._last_ack_outbound_id,
            ).to_dict(),
            id=new_frame_id("resume"),
        )
        await self._send_frame(resume)
        for frame in list(self._pending_inbound_frames.values()):
            await self._send_frame(frame)

    async def _dispatch_outbound_and_ack(self, frame: BridgeFrame) -> None:
        """Run adapter send off the receiver loop so file_get can be interleaved on this socket."""
        outbound = GatewayOutboundMessage.from_payload(frame.payload)
        try:
            await self.runtime.dispatch_outbound(outbound)
            await self._send_frame(
                BridgeFrame(
                    type=FRAME_ACK,
                    payload=AckPayload(ref_type="outbound", ref_id=frame.id, status=ACK_STATUS_OK).to_dict(),
                    id=new_frame_id("ack"),
                )
            )
            self._last_ack_outbound_id = frame.id
        except Exception as exc:
            logger.exception("Outbound dispatch failed: %s", exc)
            error_code = "route_not_found" if "Missing gateway route" in str(exc) else "adapter_send_failed"
            try:
                await self._send_frame(
                    BridgeFrame(
                        type=FRAME_ACK,
                        payload=AckPayload(
                            ref_type="outbound",
                            ref_id=frame.id,
                            status="failed",
                            error={"code": error_code, "message": str(exc)},
                        ).to_dict(),
                        id=new_frame_id("ack"),
                    )
                )
            except Exception as send_exc:
                logger.warning("Failed to send outbound failure ack: %s", send_exc)

    async def _handle_frame(self, frame: BridgeFrame) -> None:
        if frame.type == FRAME_ACK:
            ref_id = str(frame.payload.get("ref_id") or "")
            waiter = self._ack_waiters.pop(ref_id, None)
            if waiter and not waiter.done():
                waiter.set_result(frame)
            if str(frame.payload.get("status") or "") in {ACK_STATUS_OK, ACK_STATUS_DUPLICATE}:
                self._last_ack_inbound_id = ref_id
                self._pending_inbound_frames.pop(ref_id, None)
            return

        if frame.type == FRAME_SESSION_OPEN_ACK:
            waiter = self._session_waiters.pop(frame.id, None)
            if waiter and not waiter.done():
                waiter.set_result(frame)
            return

        if frame.type == FRAME_FILE_PUT_ACK:
            waiter = self._file_put_waiters.pop(frame.id, None)
            if waiter and not waiter.done():
                waiter.set_result(frame)
            return

        if frame.type == FRAME_FILE_GET_CHUNK:
            request_id = str(frame.payload.get("request_id") or "")
            state = self._downloads.get(request_id)
            if state is None:
                return
            seq = int(frame.payload.get("seq") or 0)
            data_b64 = str(frame.payload.get("data_b64") or "")
            state.chunks[seq] = base64.b64decode(data_b64.encode("utf-8"))
            filename = frame.payload.get("filename")
            mime_type = frame.payload.get("mime_type")
            if isinstance(filename, str):
                state.filename = filename
            if isinstance(mime_type, str):
                state.mime_type = mime_type
            return

        if frame.type == FRAME_FILE_GET_ACK:
            request_id = str(frame.payload.get("request_id") or "")
            state = self._downloads.get(request_id)
            if state and state.ack_future and not state.ack_future.done():
                state.ack_future.set_result(frame)
            return

        if frame.type == FRAME_OUTBOUND:
            # Must not await dispatch_outbound on the receiver task: adapters may call
            # download_file(), which reuses this WebSocket for file_get chunks. Blocking
            # here deadlocks the connection (receiver cannot read chunk frames).
            asyncio.create_task(self._dispatch_outbound_and_ack(frame))
            return

        if frame.type == FRAME_HEARTBEAT:
            await self._send_frame(
                BridgeFrame(type=FRAME_HEARTBEAT, payload=frame.payload, id=new_frame_id("hb")),
            )
            return

    async def _receiver_loop(self) -> None:
        while True:
            raw = await self._ws.recv()
            frame = BridgeFrame.from_json(raw)
            await self._handle_frame(frame)

    async def _heartbeat_loop(self) -> None:
        seq = 0
        while True:
            await asyncio.sleep(10)
            seq += 1
            await self._send_frame(
                BridgeFrame(type=FRAME_HEARTBEAT, payload={"seq": seq}, id=new_frame_id("hb")),
            )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(settings.gateway_bridge_url, max_size=None) as websocket:
                    self._ws = websocket
                    await self._handshake()
                    self._receiver_task = asyncio.create_task(self._receiver_loop())
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    await self._receiver_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Bridge client disconnected: %s", exc)
            finally:
                self._ready_event.clear()
                self._ws = None
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                    self._heartbeat_task = None
                if self._receiver_task:
                    await asyncio.gather(self._receiver_task, return_exceptions=True)
                    self._receiver_task = None
            if not self._stop_event.is_set():
                await asyncio.sleep(settings.gateway_reconnect_seconds)

    async def start(self) -> None:
        """Start background reconnect loop."""
        self._stop_event.clear()
        self._runner_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop connection loop and close websocket."""
        self._stop_event.set()
        self._ready_event.clear()
        if self._receiver_task:
            self._receiver_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._runner_task:
            self._runner_task.cancel()
            await asyncio.gather(self._runner_task, return_exceptions=True)
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def ensure_ready(self) -> None:
        """Wait until the bridge connection is ready."""
        await self._ready_event.wait()

    async def open_session(self, *, adapter_key: str, platform: str, title: str | None, meta: dict[str, Any] | None = None) -> str:
        """Open one business session through the bridge."""
        await self.ensure_ready()
        frame = BridgeFrame(
            type=FRAME_SESSION_OPEN,
            payload=SessionOpenPayload(
                adapter_key=adapter_key,
                platform=platform,
                title=title,
                meta=dict(meta or {}),
            ).to_dict(),
            id=new_frame_id("sess_open"),
        )
        future: asyncio.Future[BridgeFrame] = asyncio.get_running_loop().create_future()
        self._session_waiters[frame.id] = future
        await self._send_frame(frame)
        response = await future
        payload = response.payload
        if not bool(payload.get("ok")):
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raise RuntimeError(str(error.get("message") or "Failed to open session"))
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("Bridge did not return a valid session_id")
        return session_id

    async def send_inbound(self, message: GatewayInboundMessage) -> None:
        """Send one inbound frame and wait for ack."""
        await self.ensure_ready()
        frame = BridgeFrame(type=FRAME_INBOUND, payload=message.to_payload(), id=new_frame_id("in"))
        future: asyncio.Future[BridgeFrame] = asyncio.get_running_loop().create_future()
        self._ack_waiters[frame.id] = future
        self._pending_inbound_frames[frame.id] = frame
        await self._send_frame(frame)
        response = await future
        status = str(response.payload.get("status") or "")
        if status not in {ACK_STATUS_OK, ACK_STATUS_DUPLICATE}:
            error = response.payload.get("error") if isinstance(response.payload.get("error"), dict) else {}
            raise RuntimeError(str(error.get("message") or "Inbound frame failed"))

    async def upload_file(
        self,
        *,
        session_id: str,
        adapter_key: str,
        message_id: str,
        content: bytes,
        filename: str,
        mime_type: str | None,
    ) -> str:
        """Upload one file over the file_put sub-protocol."""
        await self.ensure_ready()
        if len(content) > int(self._limits.get("max_file_bytes") or settings.bridge_max_file_bytes):
            raise RuntimeError("File exceeds bridge max_file_bytes")
        init_frame = BridgeFrame(
            type=FRAME_FILE_PUT_INIT,
            payload=GatewayFilePutInit(
                session_id=session_id,
                adapter_key=adapter_key,
                message_id=message_id,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(content),
                sha256_hex=sha256_hex(content),
            ).to_payload(),
            id=new_frame_id("put_init"),
        )
        init_future: asyncio.Future[BridgeFrame] = asyncio.get_running_loop().create_future()
        self._file_put_waiters[init_frame.id] = init_future
        await self._send_frame(init_frame)
        init_response = await init_future
        upload_id = init_response.payload.get("upload_id")
        if not isinstance(upload_id, str) or not upload_id:
            raise RuntimeError("Bridge did not return upload_id")

        chunk_size = int(self._limits.get("max_chunk_bytes") or settings.bridge_max_chunk_bytes)
        total_chunks = 0
        for seq, offset in enumerate(range(0, len(content), chunk_size)):
            piece = content[offset : offset + chunk_size]
            total_chunks += 1
            chunk_frame = BridgeFrame(
                type=FRAME_FILE_PUT_CHUNK,
                payload=GatewayFilePutChunk(
                    upload_id=upload_id,
                    seq=seq,
                    data_b64=base64.b64encode(piece).decode("utf-8"),
                    chunk_bytes=len(piece),
                ).to_payload(),
                id=new_frame_id("put_chunk"),
            )
            chunk_future: asyncio.Future[BridgeFrame] = asyncio.get_running_loop().create_future()
            self._file_put_waiters[chunk_frame.id] = chunk_future
            await self._send_frame(chunk_frame)
            chunk_response = await chunk_future
            if str(chunk_response.payload.get("status") or "") not in {ACK_STATUS_OK, ACK_STATUS_DUPLICATE}:
                raise RuntimeError("Bridge rejected file chunk")

        commit_frame = BridgeFrame(
            type=FRAME_FILE_PUT_COMMIT,
            payload=GatewayFilePutCommit(upload_id=upload_id, total_chunks=total_chunks).to_payload(),
            id=new_frame_id("put_commit"),
        )
        commit_future: asyncio.Future[BridgeFrame] = asyncio.get_running_loop().create_future()
        self._file_put_waiters[commit_frame.id] = commit_future
        await self._send_frame(commit_frame)
        commit_response = await commit_future
        status = str(commit_response.payload.get("status") or "")
        file_id = commit_response.payload.get("file_id")
        if status not in {ACK_STATUS_OK, ACK_STATUS_DUPLICATE} or not isinstance(file_id, str) or not file_id:
            raise RuntimeError("Bridge failed to commit file upload")
        return file_id

    async def download_file(self, *, session_id: str, file_id: str) -> tuple[bytes, str | None, str | None]:
        """Download one persisted file over the file_get sub-protocol."""
        await self.ensure_ready()
        request_id = new_frame_id("get")
        ack_future: asyncio.Future[BridgeFrame] = asyncio.get_running_loop().create_future()
        self._downloads[request_id] = _DownloadState(
            request_id=request_id,
            file_id=file_id,
            ack_future=ack_future,
        )
        frame = BridgeFrame(
            type=FRAME_FILE_GET,
            payload=GatewayFileGetRequest(session_id=session_id, file_id=file_id, request_id=request_id).to_payload(),
            id=new_frame_id("get_req"),
        )
        await self._send_frame(frame)
        ack_frame = await ack_future
        payload = ack_frame.payload
        status = str(payload.get("status") or "")
        state = self._downloads.pop(request_id)
        if status != ACK_STATUS_OK:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raise RuntimeError(str(error.get("message") or "Bridge failed to download file"))
        content = b"".join(state.chunks[index] for index in sorted(state.chunks.keys()))
        return content, state.filename, state.mime_type


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fairyclaw.gateway.runtime import GatewayRuntime

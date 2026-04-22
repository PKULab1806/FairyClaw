# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Business-side UserGateway: single WebSocket bridge + inbound/outbound user channel."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from fairyclaw.config.settings import settings
from fairyclaw.core.agent.context.history_ir import ToolCallRound
from fairyclaw.core.agent.session.global_state import get_or_create_subtask_state
from fairyclaw.core.agent.session.session_role import resolve_session_role_policy
from fairyclaw.core.gateway_protocol.control_envelope import (
    EVENT_TYPE_SUBAGENT_TASKS,
    EVENT_TYPE_TELEMETRY,
    EVENT_TYPE_TIMER_TICK,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
    HeartbeatInfo,
    SubagentTaskState,
    TelemetrySnapshot,
    TimerTickEnvelope,
    ToolCallEnvelope,
    ToolResultEnvelope,
    parse_tool_arguments_json,
)
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
    FRAME_GATEWAY_CONTROL,
    FRAME_GATEWAY_CONTROL_ACK,
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
    OUTBOUND_BROADCAST_SESSION_ID,
    now_ms,
    HelloAckPayload,
    SessionOpenAckPayload,
    SessionOpenPayload,
    new_frame_id,
)
from fairyclaw.core.events.bus import SessionEventBus
from fairyclaw.infrastructure.database.repository import (
    EventRepository,
    GatewaySessionRouteRepository,
    SessionRepository,
)
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _task_type_from_status(status: str) -> str:
    raw = (status or "").strip().lower()
    if raw.startswith("running:"):
        task_type = raw.split(":", 1)[1].strip()
        return task_type or "general"
    return "general"


def _status_display(status: str) -> str:
    raw = (status or "").strip().lower()
    if raw in {"completed", "failed", "cancelled"}:
        return raw
    if raw.startswith("running"):
        return "running"
    if raw == "active":
        return "running"
    return raw or "running"


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


class UserGateway:
    """Own the business-side WebSocket bridge and user-visible inbound/outbound."""

    def __init__(self, bus: SessionEventBus, gateway_control: Any | None = None) -> None:
        self._bus = bus
        self._gateway_control = gateway_control
        self.ingress = GatewayIngressService()
        self.files = GatewayFileService()
        self._active_websocket: WebSocket | None = None
        self._active_gateway_id: str | None = None
        self._connection_id: str | None = None
        self._state_lock = asyncio.Lock()
        self._processed_inbound = _RecentFrameSet()
        self._acked_outbound: set[str] = set()
        self._outbound_backlog: deque[BridgeFrame] = deque(maxlen=settings.bridge_outbound_backlog_size)
        # Starlette WebSocket send is not safe concurrently with other sends; telemetry / outbound
        # share the bridge with the receive loop.
        self._bridge_send_lock = asyncio.Lock()

    async def _bridge_send_text(self, websocket: WebSocket, text: str) -> None:
        async with self._bridge_send_lock:
            await websocket.send_text(text)

    async def _enrich_outbound_with_route(self, message: GatewayOutboundMessage) -> GatewayOutboundMessage:
        """Attach adapter_key/sender_ref from DB when missing so a split gateway can still dispatch."""
        if message.session_id == OUTBOUND_BROADCAST_SESSION_ID:
            return replace(message, adapter_key="http", sender_ref=None)
        if message.adapter_key and message.sender_ref is not None:
            return message
        async with AsyncSessionLocal() as db:
            repo = GatewaySessionRouteRepository(db)
            model = await repo.resolve(message.session_id)
            if model is None or not model.adapter_key:
                return message
            resolved_key = model.adapter_key
            sr = dict(model.sender_ref) if model.sender_ref else {}
        return replace(
            message,
            adapter_key=message.adapter_key or resolved_key,
            sender_ref=message.sender_ref if message.sender_ref is not None else (sr if sr else None),
        )

    async def push_outbound(self, message: GatewayOutboundMessage) -> None:
        """Queue and send one outbound business message."""
        enriched = await self._enrich_outbound_with_route(message)
        frame = BridgeFrame(type=FRAME_OUTBOUND, payload=enriched.to_payload(), id=new_frame_id("out"))
        async with self._state_lock:
            self._outbound_backlog.append(frame)
            websocket = self._active_websocket
        if websocket is not None:
            await self._bridge_send_text(websocket, frame.to_json())

    async def emit_file(self, session_id: str, file_id: str) -> None:
        """Deliver one stored file to the user channel.

        Main session: outbound uses that ``session_id``. Sub-session: outbound keeps the **child**
        ``session_id`` and file id; the Web gateway delivers to parent-bound sockets (no file clone).
        """
        policy = resolve_session_role_policy(session_id)
        if policy.can_callback_user:
            await self.push_outbound(GatewayOutboundMessage.file(session_id=session_id, file_id=file_id))
            return

        async with AsyncSessionLocal() as db:
            route_repo = GatewaySessionRouteRepository(db)
            parent_session_id = await route_repo.get_parent_session_id(session_id)
        if not parent_session_id:
            logger.warning("emit_file: no parent route for sub-session=%s", session_id)
            return

        await self.push_outbound(GatewayOutboundMessage.file(session_id=session_id, file_id=file_id))

    async def emit_assistant_text(self, session_id: str, text: str) -> None:
        """Push assistant text to the user channel (main and sub-session; Web gateway routes sub-id pushes)."""
        t = text.strip()
        if not t:
            return
        await self.push_outbound(GatewayOutboundMessage.text(session_id=session_id, text=t))

    async def emit_tool_call(
        self,
        session_id: str,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments_json: str,
    ) -> None:
        """Notify gateway UI before tool execution (main and sub-session)."""
        arguments = parse_tool_arguments_json(arguments_json)
        env = ToolCallEnvelope(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        await self.push_outbound(
            GatewayOutboundMessage.event(
                session_id,
                event_type=EVENT_TYPE_TOOL_CALL,
                content=env.to_content_dict(),
            )
        )

    async def emit_tool_result(self, session_id: str, tool_round: ToolCallRound) -> None:
        """Notify gateway UI after tool execution (main and sub-session)."""
        err_msg = None if tool_round.success else (tool_round.tool_result or "error")
        res_env = ToolResultEnvelope(
            tool_call_id=tool_round.call_id,
            tool_name=tool_round.tool_name,
            ok=tool_round.success,
            result=tool_round.tool_result if tool_round.success else None,
            error_message=err_msg if not tool_round.success else None,
        )
        await self.push_outbound(
            GatewayOutboundMessage.event(
                session_id,
                event_type=EVENT_TYPE_TOOL_RESULT,
                content=res_env.to_content_dict(),
            )
        )

    async def emit_timer_tick(
        self,
        session_id: str,
        *,
        job_id: str,
        mode: str,
        owner_session_id: str,
        creator_session_id: str,
        run_index: int,
        payload: str = "",
        next_fire_at_ms: int | None = None,
    ) -> None:
        """Notify web UI that a timer tick was injected for this session."""
        env = TimerTickEnvelope(
            job_id=job_id,
            mode=mode,
            owner_session_id=owner_session_id,
            creator_session_id=creator_session_id,
            run_index=max(1, int(run_index)),
            payload=(payload or "").strip() or None,
            next_fire_at_ms=next_fire_at_ms,
        )
        await self.push_outbound(
            GatewayOutboundMessage.event(
                session_id,
                event_type=EVENT_TYPE_TIMER_TICK,
                content=env.to_content_dict(),
            )
        )

    async def emit_telemetry_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        """Broadcast telemetry to all web clients (Gateway uses ``OUTBOUND_BROADCAST_SESSION_ID``)."""
        await self.push_outbound(
            GatewayOutboundMessage.event(
                OUTBOUND_BROADCAST_SESSION_ID,
                event_type=EVENT_TYPE_TELEMETRY,
                content=snapshot.to_dict(),
            )
        )

    async def emit_subagent_tasks_snapshot(self, parent_session_id: str) -> None:
        """Push sub-agent row snapshot for one main session to the web UI."""
        rows = await self.collect_subagent_task_rows(parent_session_id)
        await self.push_outbound(
            GatewayOutboundMessage.event(
                parent_session_id,
                event_type=EVENT_TYPE_SUBAGENT_TASKS,
                content={"tasks": rows},
            )
        )

    async def collect_subagent_task_rows(self, parent_session_id: str) -> list[dict[str, Any]]:
        """Collect sub-agent task rows for one main session."""
        if not resolve_session_role_policy(parent_session_id).can_callback_user:
            return []
        subtask_state = get_or_create_subtask_state(parent_session_id)
        record_by_id = {record.sub_session_id: record for record in subtask_state.list_records()}
        async with AsyncSessionLocal() as db:
            route_repo = GatewaySessionRouteRepository(db)
            sess_repo = SessionRepository(db)
            event_repo = EventRepository(db)
            children = await route_repo.list_by_parent_session(parent_session_id)
            rows: list[SubagentTaskState] = []
            for ch in children:
                sid = ch.session_id
                model = await sess_repo.get(sid)
                ev_count, last_ev_ms = await event_repo.session_event_stats(sid)
                title = (model.title if model else None) or ""
                updated = int(model.updated_at.timestamp() * 1000) if model else now_ms()
                raw_meta = getattr(model, "meta", None) if model is not None else None
                meta = raw_meta if isinstance(raw_meta, dict) else {}
                record = record_by_id.get(sid)
                status = (record.status if record else "").strip()
                if not status and isinstance(meta.get("subtask_status"), str):
                    status = meta.get("subtask_status", "").strip()
                if not status:
                    status = "running:general"
                task_type = _task_type_from_status(status)
                if not record and isinstance(meta.get("task_type"), str):
                    task_type = _task_type_from_status(f"running:{meta.get('task_type')}")
                instruction = (record.instruction if record else "").strip()
                if not instruction and isinstance(meta.get("instruction"), str):
                    instruction = meta.get("instruction").strip()
                if title.lower().startswith("sub-agent of "):
                    title = ""
                if instruction:
                    label = f"{task_type} | {instruction}"
                elif title:
                    label = f"{task_type} | {title}"
                else:
                    label = f"{task_type} | {sid}"
                rows.append(
                    SubagentTaskState(
                        task_id=sid,
                        parent_session_id=parent_session_id,
                        label=label[:128],
                        status=status[:64],
                        status_display=_status_display(status),
                        task_type=task_type[:32],
                        instruction=(instruction[:200] if instruction else None),
                        updated_at_ms=updated,
                        child_session_id=sid,
                        detail=(record.summary if record and record.summary else None),
                        event_count=ev_count,
                        last_event_at_ms=last_ev_ms,
                    )
                )
        return [r.to_dict() for r in rows]

    async def _send_frame(self, websocket: WebSocket, frame: BridgeFrame) -> None:
        await self._bridge_send_text(websocket, frame.to_json())

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
            await self._bridge_send_text(websocket, frame.to_json())

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Accept and process one gateway WebSocket connection."""
        await websocket.accept()
        logger.debug("Gateway bridge WebSocket accepted peer=%s", websocket.client)
        loop_exit: str | None = None
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    frame = BridgeFrame.from_json(raw)
                except Exception as exc:
                    logger.warning("Gateway bridge: dropped non-JSON or invalid frame: %s", exc)
                    continue
                try:
                    await self._handle_frame(websocket, frame)
                except WebSocketDisconnect:
                    raise
                except Exception:
                    logger.exception("Gateway bridge: frame handler failed; closing connection")
                    loop_exit = "handler_error"
                    break
        except WebSocketDisconnect as exc:
            code = getattr(exc, "code", None)
            reason = getattr(exc, "reason", "") or ""
            logger.debug(
                "Gateway bridge disconnected (WebSocketDisconnect) code=%s reason=%r",
                code,
                reason,
            )
        else:
            if loop_exit == "handler_error":
                logger.warning(
                    "Gateway bridge: message loop exited after a frame handler error "
                    "(see exception above)."
                )
        finally:
            if loop_exit == "handler_error":
                logger.warning(
                    "Gateway bridge connection ending after handler error (see exception above)"
                )
            async with self._state_lock:
                if self._active_websocket is websocket:
                    self._active_websocket = None
                    self._active_gateway_id = None
                    self._connection_id = None

    async def _handle_frame(self, websocket: WebSocket, frame: BridgeFrame) -> None:
        frame_type = frame.type
        if frame_type == FRAME_HELLO:
            payload = frame.payload
            token = payload.get("token")
            if token != settings.bridge_token:
                logger.warning(
                    "Gateway bridge HELLO rejected: invalid bridge token (check FAIRYCLAW_BRIDGE_TOKEN "
                    "matches on Business and Gateway)"
                )
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
            logger.debug(
                "Gateway bridge handshake ok gateway_id=%s connection_id=%s",
                gateway_id or "(empty)",
                connection_id,
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
                await self.ingress.submit_message(message, bus=self._bus)
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

        if frame_type == FRAME_GATEWAY_CONTROL:
            if self._gateway_control is None:
                await self._send_frame(
                    websocket,
                    BridgeFrame(
                        type=FRAME_GATEWAY_CONTROL_ACK,
                        payload={
                            "request_id": frame.id,
                            "ok": False,
                            "error": {"code": "control_unavailable", "message": "Gateway control is not configured"},
                        },
                        id=new_frame_id("gca"),
                    ),
                )
                return
            op = str(frame.payload.get("op") or "")
            body = frame.payload.get("body") if isinstance(frame.payload.get("body"), dict) else {}
            try:
                result = await self._gateway_control.handle(op, body)
                await self._send_frame(
                    websocket,
                    BridgeFrame(
                        type=FRAME_GATEWAY_CONTROL_ACK,
                        payload={"request_id": frame.id, "ok": True, "body": result},
                        id=new_frame_id("gca"),
                    ),
                )
            except Exception as exc:
                logger.exception("gateway_control failed: %s", op)
                await self._send_frame(
                    websocket,
                    BridgeFrame(
                        type=FRAME_GATEWAY_CONTROL_ACK,
                        payload={
                            "request_id": frame.id,
                            "ok": False,
                            "error": {"code": "control_failed", "message": str(exc)},
                        },
                        id=new_frame_id("gca"),
                    ),
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


def create_ws_bridge_router(gateway: UserGateway) -> APIRouter:
    """Build FastAPI router for bridge websocket endpoint."""
    router = APIRouter()

    @router.websocket(settings.bridge_ws_path)
    async def gateway_ws(websocket: WebSocket) -> None:
        await gateway.handle_connection(websocket)

    return router

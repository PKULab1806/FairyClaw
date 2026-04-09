# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Web gateway unified WebSocket: sessions, chat, files, outbound push (no REST for web)."""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from fairyclaw.config.settings import settings
from fairyclaw.core.domain import ContentSegment, SegmentType
from fairyclaw.core.gateway_protocol.models import GatewayInboundMessage, new_frame_id, now_ms
from fairyclaw.api.schemas.chat import ChatRequest
from fairyclaw.api.schemas.sessions import CreateSessionRequest
from fairyclaw.gateway.adapters.onebot_adapter import OneBotGatewayAdapter
from fairyclaw.infrastructure.database.models import EventModel, SessionModel
from fairyclaw.infrastructure.database.repository import EventRepository, GatewaySessionRouteRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

if TYPE_CHECKING:
    from fairyclaw.gateway.adapters.web_gateway_adapter import WebGatewayAdapter

logger = logging.getLogger(__name__)

OP_SESSION_CREATE = "session.create"
OP_SESSION_BIND = "session.bind"
OP_CHAT = "chat.send"
OP_FILE_UPLOAD = "file.upload"
OP_FILE_DOWNLOAD = "file.download"
OP_PING = "ping"

MSG_ACK = "ack"
MSG_ERROR = "error"
MSG_PUSH = "push"


async def run_web_gateway_socket(adapter: WebGatewayAdapter, websocket: WebSocket) -> None:
    """Handle one browser connection: JSON lines with op/id/body."""
    bound_sessions: set[str] = set()
    await adapter.register_client(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(websocket, None, "invalid_json")
                continue
            op = data.get("op")
            req_id = data.get("id")
            body = data.get("body") if isinstance(data.get("body"), dict) else {}
            try:
                result = await _dispatch_op(adapter, websocket, op, body, bound_sessions)
            except Exception as exc:
                logger.exception("web gateway op failed: %s", op)
                await _send_error(websocket, req_id, str(exc))
                continue
            if result is not None:
                await _send_ack(websocket, req_id, result)
    except WebSocketDisconnect:
        pass
    finally:
        await adapter.unregister_client(websocket)
        async with adapter._lock:
            for sid in bound_sessions:
                adapter._session_sockets[sid].discard(websocket)


async def _dispatch_op(
    adapter: WebGatewayAdapter,
    websocket: WebSocket,
    op: str | None,
    body: dict[str, Any],
    bound_sessions: set[str],
) -> dict[str, Any] | None:
    if op == OP_PING:
        return {"pong": True}
    if op == OP_SESSION_CREATE:
        payload = CreateSessionRequest(
            platform=str(body.get("platform") or "web"),
            title=body.get("title"),
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else {},
        )
        session_id = await adapter.runtime.open_session(
            adapter_key=adapter.adapter_key,
            platform=payload.platform,
            title=payload.title,
            meta=payload.meta,
        )
        return {"session_id": session_id, "title": payload.title, "created_at": now_ms()}
    if op == OP_SESSION_BIND:
        sid = str(body.get("session_id") or "").strip()
        if not sid:
            raise ValueError("session_id required")
        async with adapter._lock:
            adapter._session_sockets[sid].add(websocket)
            backlog = list(adapter._backlog.get(sid, ()))
        bound_sessions.add(sid)
        for item in backlog:
            await websocket.send_json(item)
        return {"bound": True, "session_id": sid}
    if op == OP_CHAT:
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id required")
        req = ChatRequest.model_validate({"segments": body.get("segments") or []})
        user_segments: list[ContentSegment] = []
        has_text_segment = False
        for segment in req.segments:
            if segment.type == SegmentType.TEXT.value:
                text = (segment.content or "").strip()
                if text:
                    has_text_segment = True
                user_segments.append(ContentSegment.text_segment(segment.content or ""))
            elif segment.type == SegmentType.IMAGE_URL.value and segment.image_url:
                image_url = segment.image_url.get("url") if isinstance(segment.image_url, dict) else None
                if image_url:
                    user_segments.append(ContentSegment.image_url_segment(image_url))
            elif segment.type == SegmentType.FILE.value and segment.file_id:
                user_segments.append(ContentSegment.file_segment(segment.file_id))
        await adapter.runtime.submit_inbound(
            GatewayInboundMessage(
                session_id=session_id,
                adapter_key=adapter.adapter_key,
                segments=tuple(user_segments),
                trigger_turn=bool(user_segments and has_text_segment),
                meta={"message_id": new_frame_id("ws_msg")},
            )
        )
        return {
            "status": "accepted",
            "message": (
                "Message received, processing in background."
                if user_segments and has_text_segment
                else "Message received and stored. Waiting for text input to trigger processing."
            ),
        }
    if op == OP_FILE_UPLOAD:
        session_id = str(body.get("session_id") or "").strip()
        filename = str(body.get("filename") or "unnamed")
        b64 = body.get("content_base64")
        if not session_id or not isinstance(b64, str):
            raise ValueError("session_id and content_base64 required")
        raw = base64.b64decode(b64)
        if len(raw) > settings.bridge_max_file_bytes:
            raise ValueError("file too large")
        file_id = await adapter.runtime.upload_file(
            session_id=session_id,
            adapter_key=adapter.adapter_key,
            message_id=new_frame_id("ws_file"),
            content=raw,
            filename=filename,
            mime_type=body.get("mime_type") if isinstance(body.get("mime_type"), str) else None,
        )
        return {
            "file_id": file_id,
            "filename": filename,
            "size": len(raw),
            "created_at": now_ms(),
        }
    if op == OP_FILE_DOWNLOAD:
        session_id = str(body.get("session_id") or "").strip()
        file_id = str(body.get("file_id") or "").strip()
        if not session_id or not file_id:
            raise ValueError("session_id and file_id required")
        content, filename, mime_type = await adapter.runtime.download_file(session_id=session_id, file_id=file_id)
        return {
            "file_id": file_id,
            "filename": filename or file_id,
            "mime_type": mime_type,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
    if op == "config.llm.get":
        result = await adapter.runtime.bridge.send_bridge_control("config.llm.get", {})
        return {"document": result.get("document") if isinstance(result.get("document"), dict) else {}}
    if op == "config.llm.put":
        doc = body.get("document")
        if not isinstance(doc, dict):
            raise ValueError("document object required")
        await adapter.runtime.bridge.send_bridge_control("config.llm.put", {"document": doc})
        return {"ok": True}
    if op == "config.system_env.get":
        result = await adapter.runtime.bridge.send_bridge_control("config.system_env.get", {})
        return {"env": result.get("env") if isinstance(result.get("env"), dict) else {}}
    if op == "config.system_env.put":
        env = body.get("env")
        if not isinstance(env, dict):
            raise ValueError("env object required")
        await adapter.runtime.bridge.send_bridge_control("config.system_env.put", {"env": env})
        return {"ok": True}
    if op == "config.onebot.get":
        ob = adapter.runtime.adapters.get("onebot")
        if not isinstance(ob, OneBotGatewayAdapter):
            raise RuntimeError("OneBot adapter not available")
        return {"settings": ob.get_onebot_settings()}
    if op == "config.onebot.put":
        ob = adapter.runtime.adapters.get("onebot")
        if not isinstance(ob, OneBotGatewayAdapter):
            raise RuntimeError("OneBot adapter not available")
        ob.apply_onebot_settings_then_persist(body)
        return {"ok": True}
    if op == "sessions.list":
        # Web UI: only `web` platform sessions. Kept in Gateway (not SessionRepository) per product split.
        async with AsyncSessionLocal() as db:
            stmt = (
                select(
                    SessionModel.id,
                    SessionModel.title,
                    SessionModel.created_at,
                    func.coalesce(func.max(EventModel.timestamp), SessionModel.created_at),
                    func.count(EventModel.id),
                )
                .outerjoin(EventModel, SessionModel.id == EventModel.session_id)
                .where(SessionModel.platform == "web")
                .group_by(SessionModel.id)
                .order_by(SessionModel.updated_at.desc())
            )
            rows = (await db.execute(stmt)).all()
        return {
            "sessions": [
                {
                    "session_id": row[0],
                    "title": row[1],
                    "created_at": int(row[2].timestamp() * 1000),
                    "last_activity_at": int(row[3].timestamp() * 1000),
                    "event_count": int(row[4]),
                }
                for row in rows
            ]
        }
    if op == "sessions.history":
        sid = str(body.get("session_id") or "").strip()
        if not sid:
            raise ValueError("session_id required")
        result = await adapter.runtime.bridge.send_bridge_control(
            "sessions.history",
            {"session_id": sid, "limit": body.get("limit")},
        )
        evs = result.get("events")
        return {
            "session_id": str(result.get("session_id") or sid),
            "events": evs if isinstance(evs, list) else [],
        }
    if op == "sessions.subagent_tasks":
        sid = str(body.get("session_id") or "").strip()
        if not sid:
            raise ValueError("session_id required")
        result = await adapter.runtime.bridge.send_bridge_control(
            "sessions.subagent_tasks",
            {"session_id": sid},
        )
        tasks = result.get("tasks")
        return {
            "session_id": str(result.get("session_id") or sid),
            "tasks": tasks if isinstance(tasks, list) else [],
        }
    if op == "sessions.usage":
        sid = str(body.get("session_id") or "").strip()
        if not sid:
            raise ValueError("session_id required")
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            session_ids = await _collect_usage_session_ids(db, sid)
            session_totals = await repo.usage_totals(session_ids=session_ids)
            month_totals = await repo.usage_totals(month_utc=dt.datetime.now(dt.timezone.utc))
        return {
            "session_id": sid,
            "session_prompt_tokens_used": int(session_totals.get("prompt_tokens", 0)),
            "session_completion_tokens_used": int(session_totals.get("completion_tokens", 0)),
            "session_tokens_used": int(session_totals.get("total_tokens", 0)),
            "month_prompt_tokens_used": int(month_totals.get("prompt_tokens", 0)),
            "month_completion_tokens_used": int(month_totals.get("completion_tokens", 0)),
            "month_tokens_used": int(month_totals.get("total_tokens", 0)),
        }
    if op == "capabilities.list":
        result = await adapter.runtime.bridge.send_bridge_control("capabilities.list", {})
        return {"groups": result.get("groups") if isinstance(result.get("groups"), list) else []}
    if op == "capabilities.put":
        await adapter.runtime.bridge.send_bridge_control("capabilities.put", dict(body))
        return {"ok": True}
    raise ValueError(f"unknown op: {op}")


async def _collect_usage_session_ids(db: Any, root_session_id: str) -> list[str]:
    """Return root session + all descendant sub-session ids for usage aggregation."""
    route_repo = GatewaySessionRouteRepository(db)
    queue: list[str] = [root_session_id]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        children = await route_repo.list_by_parent_session(current)
        for child in children:
            child_id = str(child.session_id or "").strip()
            if child_id and child_id not in visited:
                queue.append(child_id)
    return list(visited)


async def _send_ack(websocket: WebSocket, req_id: Any, body: dict[str, Any]) -> None:
    await websocket.send_json({"op": MSG_ACK, "id": req_id, "ok": True, "body": body})


async def _send_error(websocket: WebSocket, req_id: Any, message: str) -> None:
    await websocket.send_json({"op": MSG_ERROR, "id": req_id, "ok": False, "message": message})

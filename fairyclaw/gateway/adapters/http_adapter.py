# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""HTTP gateway adapter."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status

from fairyclaw.api.dependencies import require_auth
from fairyclaw.api.schemas.chat import ChatRequest, ChatResponse
from fairyclaw.api.schemas.files import FileInfoResponse, UploadFileResponse
from fairyclaw.api.schemas.sessions import CreateSessionRequest, CreateSessionResponse
from fairyclaw.core.domain import ContentSegment, SegmentType
from fairyclaw.core.gateway_protocol.models import GatewayInboundMessage, GatewayOutboundMessage, new_frame_id, now_ms
from fairyclaw.gateway.adapters.base import GatewayAdapter


class HttpGatewayAdapter(GatewayAdapter):
    """Expose the public HTTP API and bridge it into business inbound/outbound frames."""

    adapter_key = "http"
    kind = "http_api"

    def __init__(self) -> None:
        self._session_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._backlog: dict[str, deque[dict[str, object]]] = defaultdict(lambda: deque(maxlen=128))
        self._lock = asyncio.Lock()

    def build_router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/v1/sessions", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED)
        async def create_session(
            payload: CreateSessionRequest,
            auth: None = Depends(require_auth),
        ) -> CreateSessionResponse:
            session_id = await self.runtime.open_session(
                adapter_key=self.adapter_key,
                platform=payload.platform,
                title=payload.title,
                meta=payload.meta,
            )
            return CreateSessionResponse(session_id=session_id, title=payload.title, created_at=now_ms())

        @router.post("/v1/sessions/{session_id}/chat", response_model=ChatResponse)
        @router.post("/{session_id}/chat", response_model=ChatResponse)
        async def chat(
            session_id: str,
            request: ChatRequest,
            auth: None = Depends(require_auth),
        ) -> ChatResponse:
            user_segments: list[ContentSegment] = []
            has_text_segment = False
            for segment in request.segments:
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
            await self.runtime.submit_inbound(
                GatewayInboundMessage(
                    session_id=session_id,
                    adapter_key=self.adapter_key,
                    segments=tuple(user_segments),
                    trigger_turn=bool(user_segments and has_text_segment),
                    meta={"message_id": new_frame_id("http_msg")},
                )
            )
            return ChatResponse(
                status="accepted",
                message=(
                    "Message received, processing in background."
                    if user_segments and has_text_segment
                    else "Message received and stored. Waiting for text input to trigger processing."
                ),
            )

        @router.post("/v1/files", response_model=UploadFileResponse, status_code=status.HTTP_201_CREATED)
        async def upload_file(
            file: UploadFile = File(...),
            session_id: str = Form(...),
            auth: None = Depends(require_auth),
        ) -> UploadFileResponse:
            content = await file.read()
            file_id = await self.runtime.upload_file(
                session_id=session_id,
                adapter_key=self.adapter_key,
                message_id=new_frame_id("http_file"),
                content=content,
                filename=file.filename or "unnamed",
                mime_type=file.content_type,
            )
            return UploadFileResponse(
                file_id=file_id,
                filename=file.filename or "unnamed",
                size=len(content),
                created_at=now_ms(),
            )

        @router.get("/v1/files/{file_id}", response_model=FileInfoResponse)
        async def retrieve_file_info(
            request: Request,
            file_id: str,
            session_id: str,
            auth: None = Depends(require_auth),
        ) -> FileInfoResponse:
            content, filename, mime_type = await self.runtime.download_file(session_id=session_id, file_id=file_id)
            download_url = f"{request.url_for('http_gateway_download_file_content', file_id=file_id)}?session_id={session_id}"
            return FileInfoResponse(
                file_id=file_id,
                session_id=session_id,
                filename=filename or file_id,
                size=len(content),
                created_at=now_ms(),
                mime_type=mime_type,
                download_url=download_url,
            )

        @router.get("/v1/files/{file_id}/content", name="http_gateway_download_file_content")
        async def download_file_content(
            file_id: str,
            session_id: str,
            auth: None = Depends(require_auth),
        ) -> Response:
            content, filename, mime_type = await self.runtime.download_file(session_id=session_id, file_id=file_id)
            return Response(
                content=content,
                media_type=mime_type or "application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename or file_id}"'},
            )

        @router.websocket("/v1/sessions/{session_id}/ws")
        async def session_events(session_id: str, websocket: WebSocket) -> None:
            await websocket.accept()
            async with self._lock:
                self._session_sockets[session_id].add(websocket)
                backlog = list(self._backlog.get(session_id, ()))
            for item in backlog:
                await websocket.send_json(item)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                async with self._lock:
                    self._session_sockets[session_id].discard(websocket)

        return router

    async def send(self, outbound: GatewayOutboundMessage) -> None:
        """Push outbound payload to active session websocket subscribers."""
        if outbound.kind not in {"text", "file"}:
            raise RuntimeError(f"Unsupported outbound kind for HTTP adapter: {outbound.kind}")
        content = dict(outbound.content)
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
        payload = {
            "session_id": outbound.session_id,
            "kind": outbound.kind,
            "content": content,
            "meta": dict(outbound.meta),
        }
        async with self._lock:
            sockets = list(self._session_sockets.get(outbound.session_id, ()))
            self._backlog[outbound.session_id].append(payload)
        for websocket in sockets:
            await websocket.send_json(payload)

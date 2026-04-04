# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Business-side file transfer service for the WebSocket bridge."""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field

from fairyclaw.core.events.bus import EventType
from fairyclaw.core.events.runtime import publish_runtime_event
from fairyclaw.core.gateway_protocol.models import (
    GatewayFileGetAck,
    GatewayFileGetChunk,
    GatewayFileGetRequest,
    GatewayFilePutAck,
    GatewayFilePutCommit,
    GatewayFilePutInit,
    sha256_hex,
)
from fairyclaw.infrastructure.database.repository import FileRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal


@dataclass
class PendingUpload:
    """In-memory upload accumulator."""

    session_id: str
    adapter_key: str
    message_id: str
    filename: str
    mime_type: str | None
    size_bytes: int
    sha256_hex: str
    chunks: dict[int, bytes] = field(default_factory=dict)


class GatewayFileService:
    """Implement file_put/file_get bridge operations."""

    def __init__(self) -> None:
        self._uploads: dict[str, PendingUpload] = {}

    async def put_init(self, request: GatewayFilePutInit) -> GatewayFilePutAck:
        """Allocate one upload slot."""
        upload_id = f"upload_{uuid.uuid4().hex}"
        self._uploads[upload_id] = PendingUpload(
            session_id=request.session_id,
            adapter_key=request.adapter_key,
            message_id=request.message_id,
            filename=request.filename,
            mime_type=request.mime_type,
            size_bytes=request.size_bytes,
            sha256_hex=request.sha256_hex,
        )
        return GatewayFilePutAck(status="ok", upload_id=upload_id)

    async def put_chunk(self, upload_id: str, seq: int, data_b64: str, chunk_bytes: int) -> GatewayFilePutAck:
        """Store one upload chunk in memory."""
        upload = self._uploads.get(upload_id)
        if upload is None:
            return GatewayFilePutAck(
                status="invalid",
                upload_id=upload_id,
                seq=seq,
                error={"code": "upload_not_found", "message": f"Unknown upload_id: {upload_id}"},
            )
        if seq in upload.chunks:
            return GatewayFilePutAck(status="duplicate", upload_id=upload_id, seq=seq)
        data = base64.b64decode(data_b64.encode("utf-8"))
        if len(data) != chunk_bytes:
            return GatewayFilePutAck(
                status="invalid",
                upload_id=upload_id,
                seq=seq,
                error={"code": "chunk_size_mismatch", "message": "Decoded chunk size mismatch"},
            )
        upload.chunks[seq] = data
        return GatewayFilePutAck(status="ok", upload_id=upload_id, seq=seq)

    async def put_commit(self, request: GatewayFilePutCommit) -> GatewayFilePutAck:
        """Commit accumulated chunks and persist the final file."""
        upload = self._uploads.get(request.upload_id)
        if upload is None:
            return GatewayFilePutAck(
                status="invalid",
                upload_id=request.upload_id,
                error={"code": "upload_not_found", "message": f"Unknown upload_id: {request.upload_id}"},
            )
        if len(upload.chunks) != request.total_chunks:
            return GatewayFilePutAck(
                status="invalid",
                upload_id=request.upload_id,
                error={"code": "chunk_count_mismatch", "message": "Chunk count mismatch"},
            )
        content = b"".join(upload.chunks[index] for index in range(request.total_chunks))
        if len(content) != upload.size_bytes:
            return GatewayFilePutAck(
                status="invalid",
                upload_id=request.upload_id,
                error={"code": "file_size_mismatch", "message": "Final file size mismatch"},
            )
        if sha256_hex(content) != upload.sha256_hex:
            return GatewayFilePutAck(
                status="invalid",
                upload_id=request.upload_id,
                error={"code": "sha256_mismatch", "message": "SHA-256 checksum mismatch"},
            )

        async with AsyncSessionLocal() as db:
            repo = FileRepository(db)
            model = await repo.create(
                session_id=upload.session_id,
                filename=upload.filename,
                content=content,
                mime_type=upload.mime_type,
            )
        await publish_runtime_event(
            event_type=EventType.FILE_UPLOAD_RECEIVED,
            session_id=upload.session_id,
            payload={
                "file_id": model.id,
                "filename": model.filename,
                "mime_type": model.mime_type,
                "message_id": upload.message_id,
            },
            source=f"gateway:{upload.adapter_key}",
        )
        self._uploads.pop(request.upload_id, None)
        return GatewayFilePutAck(status="ok", upload_id=request.upload_id, file_id=model.id)

    async def get_chunks(
        self,
        request: GatewayFileGetRequest,
        *,
        chunk_size: int,
    ) -> tuple[list[GatewayFileGetChunk], GatewayFileGetAck]:
        """Read one persisted file and split it into download chunks."""
        async with AsyncSessionLocal() as db:
            repo = FileRepository(db)
            model = await repo.get_for_session(file_id=request.file_id, session_id=request.session_id)
            if model is None:
                return [], GatewayFileGetAck(
                    request_id=request.request_id,
                    file_id=request.file_id,
                    status="invalid",
                    error={"code": "file_not_found", "message": "File not found"},
                )
            chunk_list: list[GatewayFileGetChunk] = []
            for seq, offset in enumerate(range(0, len(model.content), chunk_size)):
                piece = model.content[offset : offset + chunk_size]
                chunk_list.append(
                    GatewayFileGetChunk(
                        request_id=request.request_id,
                        file_id=model.id,
                        seq=seq,
                        data_b64=base64.b64encode(piece).decode("utf-8"),
                        chunk_bytes=len(piece),
                        is_last=offset + chunk_size >= len(model.content),
                        filename=model.filename,
                        mime_type=model.mime_type,
                    )
                )
            return chunk_list, GatewayFileGetAck(
                request_id=request.request_id,
                file_id=model.id,
                status="ok",
            )

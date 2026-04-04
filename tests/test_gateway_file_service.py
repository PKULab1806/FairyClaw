# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import base64
from types import SimpleNamespace

from fairyclaw.core.gateway_protocol.files import GatewayFileService
from fairyclaw.core.gateway_protocol.models import (
    GatewayFilePutCommit,
    GatewayFilePutInit,
    sha256_hex,
)


def test_gateway_file_service_put_and_get_roundtrip(monkeypatch) -> None:
    stored: dict[str, SimpleNamespace] = {}
    published_events: list[dict[str, object]] = []
    content = b"hello bridge file"

    class FakeFileRepo:
        def __init__(self, db: object) -> None:
            self.db = db

        async def create(self, session_id: str, filename: str, content: bytes, mime_type: str | None = None) -> SimpleNamespace:
            model = SimpleNamespace(
                id="file_1",
                session_id=session_id,
                filename=filename,
                content=content,
                mime_type=mime_type,
            )
            stored[model.id] = model
            return model

        async def get_for_session(self, file_id: str, session_id: str) -> SimpleNamespace | None:
            model = stored.get(file_id)
            if model and model.session_id == session_id:
                return model
            return None

    class FakeContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    async def fake_publish_runtime_event(*, event_type, session_id: str, payload: dict[str, object], source: str) -> None:
        published_events.append(
            {
                "event_type": getattr(event_type, "value", str(event_type)),
                "session_id": session_id,
                "payload": payload,
                "source": source,
            }
        )

    monkeypatch.setattr("fairyclaw.core.gateway_protocol.files.AsyncSessionLocal", FakeContext)
    monkeypatch.setattr("fairyclaw.core.gateway_protocol.files.FileRepository", FakeFileRepo)
    monkeypatch.setattr("fairyclaw.core.gateway_protocol.files.publish_runtime_event", fake_publish_runtime_event)

    service = GatewayFileService()

    async def scenario() -> None:
        init_ack = await service.put_init(
            GatewayFilePutInit(
                session_id="sess_1",
                adapter_key="http",
                message_id="msg_1",
                filename="a.txt",
                mime_type="text/plain",
                size_bytes=len(content),
                sha256_hex=sha256_hex(content),
            )
        )
        assert init_ack.status == "ok"
        upload_id = init_ack.upload_id
        chunk_ack = await service.put_chunk(
            upload_id=upload_id,
            seq=0,
            data_b64=base64.b64encode(content).decode("utf-8"),
            chunk_bytes=len(content),
        )
        assert chunk_ack.status == "ok"
        commit_ack = await service.put_commit(GatewayFilePutCommit(upload_id=upload_id, total_chunks=1))
        assert commit_ack.status == "ok"
        assert commit_ack.file_id == "file_1"

        chunks, ack = await service.get_chunks(
            request=SimpleNamespace(session_id="sess_1", file_id="file_1", request_id="get_1"),
            chunk_size=1024,
        )
        assert ack.status == "ok"
        assert len(chunks) == 1
        assert chunks[0].filename == "a.txt"

    asyncio.run(scenario())
    assert published_events[0]["payload"]["file_id"] == "file_1"

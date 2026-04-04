# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
from types import SimpleNamespace

from fairyclaw.bridge.ws_server import WsBridgeServer


def test_ws_bridge_server_delivers_sub_session_file_via_parent_route(monkeypatch) -> None:
    async def scenario() -> None:
        server = WsBridgeServer()
        published: list[tuple[str, dict[str, str]]] = []

        async def fake_push_outbound(message) -> None:
            published.append((message.session_id, dict(message.content)))

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeRouteRepo:
            def __init__(self, db) -> None:
                self.db = db

            async def get_parent_session_id(self, session_id: str) -> str | None:
                assert session_id == "sess_sub_1"
                return "sess_main"

        class FakeFileRepo:
            def __init__(self, db) -> None:
                self.db = db

            async def clone_to_session(self, *, file_id: str, source_session_id: str, target_session_id: str):
                assert (file_id, source_session_id, target_session_id) == ("file_sub", "sess_sub_1", "sess_main")
                return SimpleNamespace(id="file_parent")

        monkeypatch.setattr("fairyclaw.bridge.ws_server.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.bridge.ws_server.GatewaySessionRouteRepository", FakeRouteRepo)
        monkeypatch.setattr("fairyclaw.bridge.ws_server.FileRepository", FakeFileRepo)
        server.push_outbound = fake_push_outbound  # type: ignore[method-assign]

        await server.deliver_file_to_user("sess_sub_1", "file_sub")

        assert published == [("sess_main", {"file_id": "file_parent"})]

    asyncio.run(scenario())

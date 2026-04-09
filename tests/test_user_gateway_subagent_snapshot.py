# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import datetime as dt
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from fairyclaw.bridge.user_gateway import UserGateway
from fairyclaw.core.agent.session.global_state import get_or_create_subtask_state


def test_subagent_snapshot_uses_distinct_label_and_terminal_status(monkeypatch) -> None:
    async def scenario() -> None:
        bus = MagicMock()
        gateway = UserGateway(bus=bus)
        published: list[dict] = []

        async def fake_push_outbound(message) -> None:
            published.append(dict(message.content))

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeRouteRepo:
            def __init__(self, db) -> None:
                self.db = db

            async def list_by_parent_session(self, parent_session_id: str):
                assert parent_session_id == "sess_main_snapshot"
                return [SimpleNamespace(session_id="sess_sub_snapshot")]

        class FakeSessionRepo:
            def __init__(self, db) -> None:
                self.db = db

            async def get(self, session_id: str):
                assert session_id == "sess_sub_snapshot"
                return SimpleNamespace(title="delegate", updated_at=dt.datetime.now(dt.timezone.utc))

        state = get_or_create_subtask_state("sess_main_snapshot")
        state.register_task("sess_sub_snapshot", "scan docs and summarize", time.time())
        state.update_status("sess_sub_snapshot", "running:general")
        state.mark_terminal("sess_sub_snapshot", "completed", "done")

        monkeypatch.setattr("fairyclaw.bridge.user_gateway.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.bridge.user_gateway.GatewaySessionRouteRepository", FakeRouteRepo)
        monkeypatch.setattr("fairyclaw.bridge.user_gateway.SessionRepository", FakeSessionRepo)
        gateway.push_outbound = fake_push_outbound  # type: ignore[method-assign]

        await gateway.emit_subagent_tasks_snapshot("sess_main_snapshot")

        assert len(published) == 1
        tasks = published[0]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["label"].startswith("general | scan docs and summarize")

    asyncio.run(scenario())


def test_subagent_snapshot_uses_persisted_terminal_status_when_memory_missing(monkeypatch) -> None:
    async def scenario() -> None:
        bus = MagicMock()
        gateway = UserGateway(bus=bus)
        published: list[dict] = []

        async def fake_push_outbound(message) -> None:
            published.append(dict(message.content))

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

        class FakeRouteRepo:
            def __init__(self, db) -> None:
                self.db = db

            async def list_by_parent_session(self, parent_session_id: str):
                assert parent_session_id == "sess_main_persisted"
                return [SimpleNamespace(session_id="sess_sub_persisted")]

        class FakeSessionRepo:
            def __init__(self, db) -> None:
                self.db = db

            async def get(self, session_id: str):
                assert session_id == "sess_sub_persisted"
                return SimpleNamespace(
                    title="Sub-agent of sess_main_persisted",
                    updated_at=dt.datetime.now(dt.timezone.utc),
                    meta={
                        "subtask_status": "completed",
                        "task_type": "general",
                        "instruction": "collect evidence",
                    },
                )

        monkeypatch.setattr("fairyclaw.bridge.user_gateway.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.bridge.user_gateway.GatewaySessionRouteRepository", FakeRouteRepo)
        monkeypatch.setattr("fairyclaw.bridge.user_gateway.SessionRepository", FakeSessionRepo)
        gateway.push_outbound = fake_push_outbound  # type: ignore[method-assign]

        await gateway.emit_subagent_tasks_snapshot("sess_main_persisted")

        assert len(published) == 1
        tasks = published[0]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["status_display"] == "completed"
        assert tasks[0]["label"].startswith("general | collect evidence")

    asyncio.run(scenario())

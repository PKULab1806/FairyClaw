# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import datetime as dt
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fairyclaw.core.domain import ContentSegment
from fairyclaw.gateway.adapters.onebot_adapter import OneBotGatewayAdapter
from fairyclaw.gateway.adapters.onebot_session_store import OnebotSessionStore
from fairyclaw.infrastructure.database.models import Base, SessionModel
from fairyclaw.infrastructure.database.repository import GatewaySessionRouteRepository, OnebotSenderActiveRepository

# Tests below send `/sess ...`; pin prefix so local config/fairyclaw.env (e.g. ONEBOT_SESSION_CMD_PREFIX) does not affect them. (e.g. ONEBOT_SESSION_CMD_PREFIX) does not affect them.


def test_onebot_sender_active_repository_and_store(monkeypatch) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sender_ref = {
            "platform": "onebot",
            "user_id": "42",
            "group_id": "7",
            "self_id": "10001",
        }

        async with session_factory() as db:
            db.add_all(
                [
                    SessionModel(id="sess_a", platform="onebot", title="A", meta={}),
                    SessionModel(id="sess_b", platform="onebot", title="B", meta={}),
                    SessionModel(id="sess_sub", platform="sub_agent", title="sub", meta={}),
                    SessionModel(id="sess_other", platform="onebot", title="Other", meta={}),
                ]
            )
            await db.commit()

            route_repo = GatewaySessionRouteRepository(db)
            await route_repo.bind(session_id="sess_a", adapter_key="onebot", sender_ref=sender_ref)
            await route_repo.bind(session_id="sess_b", adapter_key="onebot", sender_ref=sender_ref)
            await route_repo.bind(session_id="sess_sub", adapter_key=None, parent_session_id="sess_a")
            await route_repo.bind(
                session_id="sess_other",
                adapter_key="onebot",
                sender_ref={"platform": "onebot", "user_id": "99", "group_id": None, "self_id": "10001"},
            )

            active_repo = OnebotSenderActiveRepository(db)
            await active_repo.upsert(adapter_key="onebot", sender_ref=sender_ref, active_session_id="sess_b")
            model = await active_repo.get(adapter_key="onebot", sender_ref=sender_ref)
            assert model is not None
            assert model.active_session_id == "sess_b"

            items = await route_repo.list_sessions_for_onebot_sender(sender_ref=sender_ref)
            assert {item.session_id for item in items} == {"sess_a", "sess_b"}

        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_session_store.AsyncSessionLocal", session_factory)
        store = OnebotSessionStore()
        assert await store.get_active_session_id(adapter_key="onebot", sender_ref=sender_ref) == "sess_b"
        await store.clear_active_session_id(adapter_key="onebot", sender_ref=sender_ref)
        assert await store.get_active_session_id(adapter_key="onebot", sender_ref=sender_ref) is None

        await engine.dispose()

    asyncio.run(scenario())


def test_onebot_new_command_creates_session_without_submitting_inbound() -> None:
    async def scenario() -> None:
        adapter = OneBotGatewayAdapter()
        adapter.onebot_session_cmd_prefix = "/sess"
        sent_messages: list[str] = []
        active_updates: list[str] = []
        submit_calls: list[object] = []
        open_calls: list[dict[str, object]] = []

        class FakeStore:
            async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                return None

            async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str], session_id: str) -> None:
                active_updates.append(session_id)

            async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> None:
                return None

        class FakeRuntime:
            async def find_session_by_sender(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                raise AssertionError("new command should not call find_session_by_sender")

            async def open_session(self, **kwargs) -> str:
                open_calls.append(kwargs)
                return "sess_new"

            async def submit_inbound(self, message) -> None:
                submit_calls.append(message)

        async def fake_send(*, user_id: int, group_id: int | None, message: str) -> None:
            sent_messages.append(message)

        adapter.runtime = FakeRuntime()  # type: ignore[attr-defined]
        adapter.session_store = FakeStore()  # type: ignore[assignment]
        adapter._send_onebot_message = fake_send  # type: ignore[method-assign]

        await adapter._process_inbound_message(
            user_id=42,
            group_id=None,
            self_id="10001",
            message="/sess new focus-session",
        )

        assert open_calls[0]["title"] == "focus-session"
        assert active_updates == ["sess_new"]
        assert submit_calls == []
        assert "sess_new" in sent_messages[0]

    asyncio.run(scenario())


def test_onebot_checkout_command_updates_active_session(monkeypatch) -> None:
    async def scenario() -> None:
        adapter = OneBotGatewayAdapter()
        adapter.onebot_session_cmd_prefix = "/sess"
        active_updates: list[str] = []
        sent_messages: list[str] = []

        class FakeStore:
            async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                return None

            async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str], session_id: str) -> None:
                active_updates.append(session_id)

            async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> None:
                return None

        class FakeRuntime:
            async def submit_inbound(self, message) -> None:
                raise AssertionError("checkout command should not submit inbound")

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeRouteRepo:
            def __init__(self, db: object) -> None:
                self.db = db

            async def get_for_onebot_sender(self, *, session_id: str, sender_ref: dict[str, str]):
                if session_id == "sess_target":
                    return SimpleNamespace(session_id=session_id)
                return None

        async def fake_send(*, user_id: int, group_id: int | None, message: str) -> None:
            sent_messages.append(message)

        adapter.runtime = FakeRuntime()  # type: ignore[attr-defined]
        adapter.session_store = FakeStore()  # type: ignore[assignment]
        adapter._send_onebot_message = fake_send  # type: ignore[method-assign]
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.GatewaySessionRouteRepository", FakeRouteRepo)

        await adapter._process_inbound_message(
            user_id=42,
            group_id=None,
            self_id="10001",
            message="/sess checkout sess_target",
        )

        assert active_updates == ["sess_target"]
        assert "sess_target" in sent_messages[0]

    asyncio.run(scenario())


def test_onebot_checkout_by_session_title(monkeypatch) -> None:
    async def scenario() -> None:
        adapter = OneBotGatewayAdapter()
        adapter.onebot_session_cmd_prefix = "/sess"
        active_updates: list[str] = []
        sent_messages: list[str] = []

        class FakeStore:
            async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                return None

            async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str], session_id: str) -> None:
                active_updates.append(session_id)

            async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> None:
                return None

        class FakeRuntime:
            async def submit_inbound(self, message) -> None:
                raise AssertionError("checkout command should not submit inbound")

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        now = dt.datetime(2026, 4, 2, 12, 0, 0)

        class FakeRouteRepo:
            def __init__(self, db: object) -> None:
                self.db = db

            async def list_sessions_for_onebot_sender(self, *, sender_ref: dict[str, str]):
                return [
                    SimpleNamespace(session_id="sess_alpha", title="Alpha chat", updated_at=now),
                    SimpleNamespace(session_id="sess_beta", title="Beta notes", updated_at=now),
                ]

            async def get_for_onebot_sender(self, *, session_id: str, sender_ref: dict[str, str]):
                return SimpleNamespace(session_id=session_id)

        async def fake_send(*, user_id: int, group_id: int | None, message: str) -> None:
            sent_messages.append(message)

        adapter.runtime = FakeRuntime()  # type: ignore[attr-defined]
        adapter.session_store = FakeStore()  # type: ignore[assignment]
        adapter._send_onebot_message = fake_send  # type: ignore[method-assign]
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.GatewaySessionRouteRepository", FakeRouteRepo)

        await adapter._process_inbound_message(
            user_id=42,
            group_id=None,
            self_id="10001",
            message="/sess co Beta notes",
        )

        assert active_updates == ["sess_beta"]
        assert "sess_beta" in sent_messages[0]

    asyncio.run(scenario())


def test_onebot_rm_resolves_by_title_and_deletes_session(monkeypatch) -> None:
    async def scenario() -> None:
        adapter = OneBotGatewayAdapter()
        adapter.onebot_session_cmd_prefix = "/sess"
        sent_messages: list[str] = []
        deleted_ids: list[str] = []
        cleared: list[bool] = []

        class FakeStore:
            async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                return "sess_rm"

            async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str], session_id: str) -> None:
                raise AssertionError("rm should not set active")

            async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> None:
                cleared.append(True)

        class FakeRuntime:
            async def submit_inbound(self, message) -> None:
                raise AssertionError("rm should not submit inbound")

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        now = dt.datetime(2026, 4, 2, 12, 0, 0)

        class FakeRouteRepo:
            def __init__(self, db: object) -> None:
                self.db = db

            async def list_sessions_for_onebot_sender(self, *, sender_ref: dict[str, str]):
                return [SimpleNamespace(session_id="sess_rm", title="Trash bin", updated_at=now)]

        class FakeSessionRepo:
            def __init__(self, db: object) -> None:
                self.db = db

            async def delete(self, session_id: str) -> bool:
                deleted_ids.append(session_id)
                return True

        async def fake_send(*, user_id: int, group_id: int | None, message: str) -> None:
            sent_messages.append(message)

        adapter.runtime = FakeRuntime()  # type: ignore[attr-defined]
        adapter.session_store = FakeStore()  # type: ignore[assignment]
        adapter._send_onebot_message = fake_send  # type: ignore[method-assign]
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.GatewaySessionRouteRepository", FakeRouteRepo)
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.SessionRepository", FakeSessionRepo)

        await adapter._process_inbound_message(
            user_id=42,
            group_id=None,
            self_id="10001",
            message="/sess rm Trash bin",
        )

        assert deleted_ids == ["sess_rm"]
        assert cleared == [True]
        assert "已删除" in sent_messages[0]

    asyncio.run(scenario())


def test_onebot_ls_command_renders_active_session_list(monkeypatch) -> None:
    async def scenario() -> None:
        adapter = OneBotGatewayAdapter()
        adapter.onebot_session_cmd_prefix = "/sess"
        sent_messages: list[str] = []

        class FakeStore:
            async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                return "sess_b"

            async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str], session_id: str) -> None:
                raise AssertionError("ls command should not write active session")

            async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> None:
                return None

        class FakeRuntime:
            async def submit_inbound(self, message) -> None:
                raise AssertionError("ls command should not submit inbound")

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeRouteRepo:
            def __init__(self, db: object) -> None:
                self.db = db

            async def list_sessions_for_onebot_sender(self, *, sender_ref: dict[str, str]):
                now = dt.datetime(2026, 4, 2, 12, 0, 0)
                return [
                    SimpleNamespace(session_id="sess_b", title="Current", updated_at=now),
                    SimpleNamespace(session_id="sess_a", title="Older", updated_at=now),
                ]

        async def fake_send(*, user_id: int, group_id: int | None, message: str) -> None:
            sent_messages.append(message)

        adapter.runtime = FakeRuntime()  # type: ignore[attr-defined]
        adapter.session_store = FakeStore()  # type: ignore[assignment]
        adapter._send_onebot_message = fake_send  # type: ignore[method-assign]
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.GatewaySessionRouteRepository", FakeRouteRepo)

        await adapter._process_inbound_message(
            user_id=42,
            group_id=None,
            self_id="10001",
            message="/sess ls",
        )

        assert "* sess_b | Current | 2026-04-02 12:00:00" in sent_messages[0]
        assert "sess_a | Older | 2026-04-02 12:00:00" in sent_messages[0]

    asyncio.run(scenario())


def test_onebot_regular_message_uses_active_session_before_fallback(monkeypatch) -> None:
    async def scenario() -> None:
        adapter = OneBotGatewayAdapter()
        submitted: list[object] = []
        cleared = False
        active_reads = 0

        class FakeStore:
            async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                nonlocal active_reads
                active_reads += 1
                return "sess_active"

            async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str], session_id: str) -> None:
                raise AssertionError("valid active session should not be overwritten")

            async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, str]) -> None:
                nonlocal cleared
                cleared = True

        class FakeRuntime:
            async def find_session_by_sender(self, *, adapter_key: str, sender_ref: dict[str, str]) -> str | None:
                raise AssertionError("active session should short-circuit fallback")

            async def open_session(self, **kwargs) -> str:
                raise AssertionError("active session should avoid open_session")

            async def submit_inbound(self, message) -> None:
                submitted.append(message)

        class FakeSessionLocal:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeRouteRepo:
            def __init__(self, db: object) -> None:
                self.db = db

            async def get_for_onebot_sender(self, *, session_id: str, sender_ref: dict[str, str]):
                return SimpleNamespace(session_id=session_id)

        async def fake_convert(*, session_id: str, message: object) -> list[ContentSegment]:
            return [ContentSegment.text_segment("hello")]

        adapter.runtime = FakeRuntime()  # type: ignore[attr-defined]
        adapter.session_store = FakeStore()  # type: ignore[assignment]
        adapter._convert_message_to_segments = fake_convert  # type: ignore[method-assign]
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.AsyncSessionLocal", FakeSessionLocal)
        monkeypatch.setattr("fairyclaw.gateway.adapters.onebot_adapter.GatewaySessionRouteRepository", FakeRouteRepo)

        await adapter._process_inbound_message(
            user_id=42,
            group_id=None,
            self_id="10001",
            message="hello",
        )

        assert active_reads == 1
        assert not cleared
        assert len(submitted) == 1
        assert submitted[0].session_id == "sess_active"

    asyncio.run(scenario())

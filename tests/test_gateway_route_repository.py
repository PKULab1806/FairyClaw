# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fairyclaw.infrastructure.database.models import Base, SessionModel
from fairyclaw.infrastructure.database.repository import GatewaySessionRouteRepository


def test_gateway_session_route_repository_resolves_parent_routes_and_sender_lookup() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            db.add(SessionModel(id="sess_main", platform="web", title="main", meta={}))
            db.add(SessionModel(id="sess_sub", platform="sub_agent", title="sub", meta={"parent_session_id": "sess_main"}))
            await db.commit()

            repo = GatewaySessionRouteRepository(db)
            await repo.bind(
                session_id="sess_main",
                adapter_key="onebot",
                sender_ref={
                    "platform": "onebot",
                    "user_id": "42",
                    "group_id": "7",
                    "self_id": "10001",
                },
            )
            await repo.bind(
                session_id="sess_sub",
                adapter_key=None,
                parent_session_id="sess_main",
            )

            resolved = await repo.resolve("sess_sub")
            assert resolved is not None
            assert resolved.session_id == "sess_main"
            assert resolved.adapter_key == "onebot"

            parent_session_id = await repo.get_parent_session_id("sess_sub")
            assert parent_session_id == "sess_main"

            by_sender = await repo.find_by_sender(
                adapter_key="onebot",
                sender_ref={
                    "platform": "onebot",
                    "user_id": "42",
                    "group_id": "7",
                    "self_id": "10001",
                },
            )
            assert by_sender is not None
            assert by_sender.session_id == "sess_main"

            children = await repo.list_by_parent_session("sess_main")
            assert [item.session_id for item in children] == ["sess_sub"]

        await engine.dispose()

    asyncio.run(scenario())


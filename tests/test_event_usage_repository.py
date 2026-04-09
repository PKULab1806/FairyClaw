# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import datetime as dt

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fairyclaw.core.domain import EventType
from fairyclaw.infrastructure.database.models import Base, EventModel, SessionModel
from fairyclaw.infrastructure.database.repository import EventRepository


def test_event_repository_usage_totals_session_and_month() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        now = dt.datetime.now(dt.timezone.utc)
        month_start = dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc)
        last_month = month_start - dt.timedelta(days=1)

        async with session_factory() as db:
            db.add(SessionModel(id="sess_a", platform="web", title="A", meta={}))
            db.add(SessionModel(id="sess_b", platform="web", title="B", meta={}))
            db.add(SessionModel(id="sess_sub", platform="sub_agent", title="S", meta={}))
            await db.commit()

            db.add(
                EventModel(
                    session_id="sess_a",
                    type=EventType.SESSION_EVENT.value,
                    role="assistant",
                    content=[{"type": "text", "text": "hi"}],
                    usage_prompt_tokens=10,
                    usage_completion_tokens=4,
                    usage_total_tokens=14,
                    timestamp=now,
                )
            )
            db.add(
                EventModel(
                    session_id="sess_a",
                    type=EventType.OPERATION_EVENT.value,
                    tool_name="x",
                    tool_args={},
                    tool_result={"ok": True},
                    usage_prompt_tokens=6,
                    usage_completion_tokens=2,
                    usage_total_tokens=8,
                    timestamp=now,
                )
            )
            db.add(
                EventModel(
                    session_id="sess_b",
                    type=EventType.SESSION_EVENT.value,
                    role="assistant",
                    content=[{"type": "text", "text": "old"}],
                    usage_prompt_tokens=100,
                    usage_completion_tokens=50,
                    usage_total_tokens=150,
                    timestamp=last_month,
                )
            )
            db.add(
                EventModel(
                    session_id="sess_sub",
                    type=EventType.SESSION_EVENT.value,
                    role="assistant",
                    content=[{"type": "text", "text": "sub"}],
                    usage_prompt_tokens=3,
                    usage_completion_tokens=1,
                    usage_total_tokens=4,
                    timestamp=now,
                )
            )
            await db.commit()

            repo = EventRepository(db)
            sess_totals = await repo.usage_totals(session_id="sess_a")
            assert sess_totals == {"prompt_tokens": 16, "completion_tokens": 6, "total_tokens": 22}

            month_totals = await repo.usage_totals(month_utc=now)
            assert month_totals == {"prompt_tokens": 19, "completion_tokens": 7, "total_tokens": 26}

            tree_totals = await repo.usage_totals(session_ids=["sess_a", "sess_sub"])
            assert tree_totals == {"prompt_tokens": 19, "completion_tokens": 7, "total_tokens": 26}

        await engine.dispose()

    asyncio.run(scenario())

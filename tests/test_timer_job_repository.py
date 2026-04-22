from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fairyclaw.infrastructure.database.models import Base, SessionModel
from fairyclaw.infrastructure.database.repository import TimerJobRepository


def test_timer_job_repository_claim_and_complete() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        now = dt.datetime.now(dt.timezone.utc)
        async with session_factory() as db:
            db.add(SessionModel(id="sess_main", platform="web", title="main", meta={}))
            db.add(SessionModel(id="sess_sub", platform="sub_agent", title="sub", meta={"parent_session_id": "sess_main"}))
            await db.commit()

            repo = TimerJobRepository(db)
            created = await repo.create(
                owner_session_id="sess_main",
                creator_session_id="sess_sub",
                mode="heartbeat",
                interval_seconds=3,
                next_fire_at=now - dt.timedelta(seconds=2),
                payload="heartbeat_test_payload",
                deadline_at=None,
                max_runs=2,
            )
            assert created.owner_session_id == "sess_main"
            assert created.creator_session_id == "sess_sub"
            assert created.payload == "heartbeat_test_payload"

            claimed = await repo.claim_due_jobs(now=now, worker_id="worker_test", limit=10)
            assert len(claimed) == 1
            assert claimed[0].id == created.id
            assert claimed[0].status == "running"

            updated = await repo.update_after_run(
                job_id=created.id,
                now=now,
                next_fire_at=now + dt.timedelta(seconds=3),
                success=True,
            )
            assert updated is not None
            assert updated.run_count == 1
            assert updated.failure_count == 0
            assert updated.active is True

            cancelled = await repo.cancel(job_id=created.id)
            assert cancelled is not None
            assert cancelled.status == "cancelled"
            assert cancelled.active is False

        await engine.dispose()

    asyncio.run(scenario())

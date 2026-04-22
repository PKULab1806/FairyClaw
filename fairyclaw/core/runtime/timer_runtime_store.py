from __future__ import annotations

import asyncio
from dataclasses import asdict
import datetime as dt
from typing import Any

from fairyclaw.infrastructure.database.models import SessionModel, TimerJobModel
from fairyclaw.infrastructure.database.repository import GatewaySessionRouteRepository, TimerJobRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

from .timer_models import (
    TimerJobRecord,
    TimerJobStatus,
    TimerMode,
    next_fire_at_for_cron,
    now_utc,
    parse_cron_5,
    to_epoch_ms,
)


class TimerRuntimeStore:
    """Process-global store for timer jobs."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        *,
        creator_session_id: str,
        mode: str,
        payload: str | None = None,
        cron_expr: str | None = None,
        interval_seconds: int | None = None,
        start_delay_seconds: int | None = None,
        deadline_seconds: int | None = None,
        max_runs: int | None = None,
    ) -> tuple[TimerJobRecord | None, str | None]:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in (TimerMode.HEARTBEAT.value, TimerMode.CRON.value):
            return None, "mode must be heartbeat or cron."

        owner_session_id = await self.resolve_owner_session_id(creator_session_id)
        if not owner_session_id:
            return None, f"Cannot resolve owner session for creator session {creator_session_id!r}."

        now = now_utc()
        delay_seconds = max(0, int(start_delay_seconds or 0))
        deadline_at: dt.datetime | None = None
        if deadline_seconds is not None:
            if int(deadline_seconds) <= 0:
                return None, "deadline_seconds must be > 0 when provided."
            deadline_at = now + dt.timedelta(seconds=int(deadline_seconds))
        max_runs_value: int | None = None
        if max_runs is not None:
            max_runs_value = int(max_runs)
            if max_runs_value <= 0:
                return None, "max_runs must be > 0 when provided."

        interval_value: int | None = None
        cron_value: str | None = None
        if normalized_mode == TimerMode.HEARTBEAT.value:
            interval_value = int(interval_seconds or 0)
            if interval_value < 2:
                return None, "heartbeat interval_seconds must be >= 2."
            next_fire_at = now + dt.timedelta(seconds=max(1, delay_seconds or interval_value))
        else:
            cron_value = str(cron_expr or "").strip()
            ok, err = parse_cron_5(cron_value)
            if not ok:
                return None, err or "invalid cron expression."
            anchor = now + dt.timedelta(seconds=delay_seconds)
            next_fire_at = next_fire_at_for_cron(cron_value, anchor)
            if next_fire_at is None:
                return None, "cron expression has no upcoming fire time in scan window."

        async with self._lock:
            async with AsyncSessionLocal() as db:
                repo = TimerJobRepository(db)
                model = await repo.create(
                    owner_session_id=owner_session_id,
                    creator_session_id=creator_session_id,
                    mode=normalized_mode,
                    cron_expr=cron_value,
                    interval_seconds=interval_value,
                    payload=str(payload or ""),
                    next_fire_at=next_fire_at,
                    deadline_at=deadline_at,
                    max_runs=max_runs_value,
                )
                return self._to_record(model), None

    async def get_job(self, *, job_id: str) -> TimerJobRecord | None:
        async with AsyncSessionLocal() as db:
            repo = TimerJobRepository(db)
            model = await repo.get(job_id)
            return self._to_record(model) if model else None

    async def list_jobs(
        self,
        *,
        owner_session_id: str,
        creator_session_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[TimerJobRecord]:
        async with AsyncSessionLocal() as db:
            repo = TimerJobRepository(db)
            rows = await repo.list_jobs(
                owner_session_id=owner_session_id,
                creator_session_id=creator_session_id,
                statuses=statuses,
                limit=limit,
            )
            return [self._to_record(row) for row in rows]

    async def claim_due_jobs(self, *, worker_id: str, limit: int = 20) -> list[TimerJobRecord]:
        async with self._lock:
            async with AsyncSessionLocal() as db:
                repo = TimerJobRepository(db)
                rows = await repo.claim_due_jobs(now=now_utc(), worker_id=worker_id, limit=limit)
                return [self._to_record(row) for row in rows]

    async def mark_job_result(
        self,
        *,
        job_id: str,
        success: bool,
        error_message: str | None = None,
    ) -> TimerJobRecord | None:
        now = now_utc()
        async with self._lock:
            async with AsyncSessionLocal() as db:
                repo = TimerJobRepository(db)
                current = await repo.get(job_id)
                if current is None:
                    return None

                terminal_status: str | None = None
                deadline_at = _as_utc_datetime(current.deadline_at)
                if deadline_at is not None and deadline_at <= now:
                    terminal_status = TimerJobStatus.COMPLETED.value
                if current.max_runs is not None and int(current.run_count or 0) + 1 >= int(current.max_runs):
                    terminal_status = TimerJobStatus.COMPLETED.value
                if not success and int(current.failure_count or 0) + 1 >= 3:
                    terminal_status = TimerJobStatus.FAILED.value

                next_fire_at: dt.datetime | None = None
                if terminal_status is None:
                    if current.mode == TimerMode.HEARTBEAT.value:
                        interval = max(2, int(current.interval_seconds or 2))
                        next_fire_at = now + dt.timedelta(seconds=interval)
                    else:
                        expr = str(current.cron_expr or "").strip()
                        next_fire_at = next_fire_at_for_cron(expr, now)
                        if next_fire_at is None:
                            terminal_status = TimerJobStatus.FAILED.value
                            error_message = error_message or "cron expression has no upcoming fire time."

                updated = await repo.update_after_run(
                    job_id=job_id,
                    now=now,
                    next_fire_at=next_fire_at,
                    success=success,
                    terminal_status=terminal_status,
                    last_error=error_message,
                )
                return self._to_record(updated) if updated else None

    async def cancel_job(self, *, job_id: str) -> TimerJobRecord | None:
        async with self._lock:
            async with AsyncSessionLocal() as db:
                repo = TimerJobRepository(db)
                model = await repo.cancel(job_id=job_id)
                return self._to_record(model) if model else None

    async def resolve_owner_session_id(self, creator_session_id: str) -> str:
        async with AsyncSessionLocal() as db:
            model = await db.get(SessionModel, creator_session_id)
            if model is None:
                return creator_session_id

            meta = dict(model.meta or {})
            parent_from_meta = str(meta.get("parent_session_id") or "").strip()
            if parent_from_meta:
                return parent_from_meta

            route_repo = GatewaySessionRouteRepository(db)
            parent_from_route = await route_repo.get_parent_session_id(creator_session_id)
            if parent_from_route:
                return parent_from_route
            return creator_session_id

    def _to_record(self, model: TimerJobModel) -> TimerJobRecord:
        return TimerJobRecord(
            job_id=model.id,
            owner_session_id=model.owner_session_id,
            creator_session_id=model.creator_session_id,
            mode=TimerMode(model.mode),
            status=TimerJobStatus(model.status),
            payload=str(model.payload or ""),
            cron_expr=model.cron_expr,
            interval_seconds=model.interval_seconds,
            next_fire_at_ms=int(model.next_fire_at.timestamp() * 1000),
            deadline_at_ms=to_epoch_ms(_as_utc_datetime(model.deadline_at)),
            max_runs=model.max_runs,
            run_count=int(model.run_count or 0),
            failure_count=int(model.failure_count or 0),
            last_error=model.last_error,
            created_at_ms=int(_as_utc_datetime(model.created_at).timestamp() * 1000),
            updated_at_ms=int(_as_utc_datetime(model.updated_at).timestamp() * 1000),
        )

    def as_dict(self, record: TimerJobRecord) -> dict[str, Any]:
        data = asdict(record)
        data["mode"] = record.mode.value
        data["status"] = record.status.value
        return data


_timer_runtime_store: TimerRuntimeStore | None = None


def get_timer_runtime_store() -> TimerRuntimeStore:
    global _timer_runtime_store
    if _timer_runtime_store is None:
        _timer_runtime_store = TimerRuntimeStore()
    return _timer_runtime_store


def _as_utc_datetime(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)

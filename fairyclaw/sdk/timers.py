from __future__ import annotations

from typing import Any

from fairyclaw.core.runtime.timer_runtime_store import TimerRuntimeStore, get_timer_runtime_store

__all__ = [
    "TimerRuntimeStore",
    "get_timer_runtime_store",
    "create_timer_job",
    "list_timer_jobs",
    "get_timer_job",
    "stop_timer_job",
    "resolve_owner_session_id",
]


async def create_timer_job(
    *,
    creator_session_id: str,
    mode: str,
    payload: str | None = None,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    start_delay_seconds: int | None = None,
    deadline_seconds: int | None = None,
    max_runs: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    store = get_timer_runtime_store()
    record, error = await store.create_job(
        creator_session_id=creator_session_id,
        mode=mode,
        payload=payload,
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
        start_delay_seconds=start_delay_seconds,
        deadline_seconds=deadline_seconds,
        max_runs=max_runs,
    )
    if record is None:
        return None, error
    return store.as_dict(record), None


async def list_timer_jobs(
    *,
    owner_session_id: str,
    creator_session_id: str | None = None,
    statuses: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    store = get_timer_runtime_store()
    rows = await store.list_jobs(
        owner_session_id=owner_session_id,
        creator_session_id=creator_session_id,
        statuses=statuses,
        limit=limit,
    )
    return [store.as_dict(row) for row in rows]


async def get_timer_job(job_id: str) -> dict[str, Any] | None:
    store = get_timer_runtime_store()
    row = await store.get_job(job_id=job_id)
    if row is None:
        return None
    return store.as_dict(row)


async def stop_timer_job(job_id: str) -> dict[str, Any] | None:
    store = get_timer_runtime_store()
    row = await store.cancel_job(job_id=job_id)
    if row is None:
        return None
    return store.as_dict(row)


async def resolve_owner_session_id(session_id: str) -> str:
    return await get_timer_runtime_store().resolve_owner_session_id(session_id)

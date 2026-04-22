from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from enum import Enum
class TimerMode(str, Enum):
    HEARTBEAT = "heartbeat"
    CRON = "cron"


class TimerJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TimerJobRecord:
    job_id: str
    owner_session_id: str
    creator_session_id: str
    mode: TimerMode
    status: TimerJobStatus
    payload: str
    cron_expr: str | None
    interval_seconds: int | None
    next_fire_at_ms: int
    deadline_at_ms: int | None
    max_runs: int | None
    run_count: int
    failure_count: int
    last_error: str | None
    created_at_ms: int
    updated_at_ms: int


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def to_epoch_ms(value: dt.datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def system_timezone() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def parse_cron_5(expr: str) -> tuple[bool, str | None]:
    parts = [part.strip() for part in str(expr or "").strip().split()]
    if len(parts) != 5:
        return False, "cron_expr must contain exactly 5 fields: minute hour day month weekday."
    validators = (
        (parts[0], 0, 59, "minute"),
        (parts[1], 0, 23, "hour"),
        (parts[2], 1, 31, "day"),
        (parts[3], 1, 12, "month"),
        (parts[4], 0, 6, "weekday"),
    )
    for token, lo, hi, name in validators:
        ok, err = _validate_field(token, lo=lo, hi=hi, field=name)
        if not ok:
            return False, err
    return True, None


def next_fire_at_for_cron(expr: str, now: dt.datetime, *, max_minutes_scan: int = 60 * 24 * 366) -> dt.datetime | None:
    parts = [part.strip() for part in str(expr or "").strip().split()]
    if len(parts) != 5:
        return None
    minute, hour, day, month, weekday = parts
    cursor = now.astimezone(system_timezone()).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(max(1, max_minutes_scan)):
        if (
            _matches_field(cursor.minute, minute, 0, 59)
            and _matches_field(cursor.hour, hour, 0, 23)
            and _matches_field(cursor.day, day, 1, 31)
            and _matches_field(cursor.month, month, 1, 12)
            and _matches_field(cursor.weekday(), weekday, 0, 6)
        ):
            return cursor.astimezone(dt.timezone.utc)
        cursor += dt.timedelta(minutes=1)
    return None


def _validate_field(token: str, *, lo: int, hi: int, field: str) -> tuple[bool, str | None]:
    normalized = token.strip()
    if not normalized:
        return False, f"{field} field is empty."
    for chunk in normalized.split(","):
        chunk = chunk.strip()
        if not chunk:
            return False, f"{field} field has empty chunk."
        if chunk == "*":
            continue
        if "/" in chunk:
            base, step_text = chunk.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                return False, f"{field} step must be a positive integer."
            if base == "*":
                continue
            if "-" in base:
                ok, _ = _parse_range(base, lo, hi)
                if not ok:
                    return False, f"{field} range is invalid: {base}"
                continue
            if not _is_int_in_range(base, lo, hi):
                return False, f"{field} value out of range: {base}"
            continue
        if "-" in chunk:
            ok, _ = _parse_range(chunk, lo, hi)
            if not ok:
                return False, f"{field} range is invalid: {chunk}"
            continue
        if not _is_int_in_range(chunk, lo, hi):
            return False, f"{field} value out of range: {chunk}"
    return True, None


def _matches_field(value: int, token: str, lo: int, hi: int) -> bool:
    for chunk in token.split(","):
        chunk = chunk.strip()
        if chunk == "*":
            return True
        if "/" in chunk:
            base, step_text = chunk.split("/", 1)
            step = int(step_text)
            if base == "*":
                if (value - lo) % step == 0:
                    return True
                continue
            if "-" in base:
                ok, parsed = _parse_range(base, lo, hi)
                if not ok or parsed is None:
                    continue
                start, end = parsed
                if start <= value <= end and (value - start) % step == 0:
                    return True
                continue
            if _is_int_in_range(base, lo, hi):
                base_value = int(base)
                if value >= base_value and (value - base_value) % step == 0:
                    return True
            continue
        if "-" in chunk:
            ok, parsed = _parse_range(chunk, lo, hi)
            if ok and parsed is not None:
                start, end = parsed
                if start <= value <= end:
                    return True
            continue
        if _is_int_in_range(chunk, lo, hi) and int(chunk) == value:
            return True
    return False


def _parse_range(value: str, lo: int, hi: int) -> tuple[bool, tuple[int, int] | None]:
    left, right = value.split("-", 1)
    if not _is_int_in_range(left, lo, hi):
        return False, None
    if not _is_int_in_range(right, lo, hi):
        return False, None
    start = int(left)
    end = int(right)
    if start > end:
        return False, None
    return True, (start, end)


def _is_int_in_range(value: str, lo: int, hi: int) -> bool:
    if not value.isdigit():
        return False
    iv = int(value)
    return lo <= iv <= hi

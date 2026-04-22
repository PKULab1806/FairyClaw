from __future__ import annotations

import datetime as dt

from fairyclaw.core.runtime.timer_models import next_fire_at_for_cron, parse_cron_5


def test_parse_cron_5_accepts_common_patterns() -> None:
    ok, err = parse_cron_5("*/5 * * * *")
    assert ok is True
    assert err is None

    ok, err = parse_cron_5("0 9 * * 1-5")
    assert ok is True
    assert err is None


def test_parse_cron_5_rejects_invalid_shapes() -> None:
    ok, err = parse_cron_5("* * * *")
    assert ok is False
    assert "5 fields" in str(err)

    ok, err = parse_cron_5("70 * * * *")
    assert ok is False
    assert "minute" in str(err)


def test_next_fire_at_for_cron_returns_future_time() -> None:
    now = dt.datetime(2026, 4, 21, 10, 3, tzinfo=dt.timezone.utc)
    nxt = next_fire_at_for_cron("*/10 * * * *", now)
    assert nxt is not None
    assert nxt > now

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for session history utilities."""

from __future__ import annotations

from fairyclaw.session_history_utils import (
    events_fingerprint,
    last_assistant_reply_from_history_events,
)


def test_last_assistant_reply_from_history_events() -> None:
    ev = [
        {"kind": "session_event", "role": "user", "text": "hi", "ts_ms": 1},
        {"kind": "session_event", "role": "assistant", "text": "a1", "ts_ms": 2},
        {"kind": "session_event", "role": "assistant", "text": "a2", "ts_ms": 3},
    ]
    assert last_assistant_reply_from_history_events(ev) == "a2"
    assert last_assistant_reply_from_history_events(None) is None
    tool_only = [
        {"kind": "session_event", "role": "user", "text": "hi", "ts_ms": 1},
        {"kind": "operation_event", "tool_name": "t", "result_preview": "tool_out", "ts_ms": 2},
    ]
    assert last_assistant_reply_from_history_events(tool_only) == "tool_out"
    assert (
        last_assistant_reply_from_history_events(
            [{"kind": "session_event", "role": "system", "text": "notice", "ts_ms": 1}]
        )
        == "notice"
    )


def test_events_fingerprint_stable() -> None:
    ev = [{"kind": "session_event", "role": "user", "text": "hi", "ts_ms": 1}]
    assert events_fingerprint(ev) == events_fingerprint(ev)

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session history utilities: reply extraction and fingerprinting."""

from __future__ import annotations

import json
from typing import Any


def last_assistant_reply_from_history_events(events: list[Any] | None) -> str | None:
    """Last model-visible line from history rows (chronological order).

    Prefer the last non-user ``session_event`` with non-empty ``text`` (``assistant`` or
    ``system``). If none, use the last non-empty ``operation_event.result_preview`` so
    tool-only turns still surface something in the CLI.
    """
    if not isinstance(events, list):
        return None
    last_msg: str | None = None
    last_tool: str | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        if kind == "session_event":
            role = str(ev.get("role") or "").strip().lower()
            if role == "user":
                continue
            if role not in ("assistant", "system"):
                continue
            t = (ev.get("text") or "").strip()
            if t:
                last_msg = t
        elif kind == "operation_event":
            rp = ev.get("result_preview")
            if isinstance(rp, str) and rp.strip():
                last_tool = rp.strip()
    return last_msg or last_tool


def events_fingerprint(events: list[Any]) -> str:
    """Stable string for comparing history snapshots (tail to avoid huge payloads)."""
    if not events:
        return "0"
    tail = events[-16:] if len(events) > 16 else events
    return json.dumps(tail, sort_keys=True, ensure_ascii=False)

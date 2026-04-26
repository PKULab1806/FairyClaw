#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Per-session store for segments unloaded by context compression."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from fairyclaw.sdk.tools import resolve_memory_root

logger = logging.getLogger(__name__)

_STATE_SUBDIR = ".session_unloaded_segments"


def _session_id_filename(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    prefix = "".join(c if c.isalnum() or c in "._-" else "_" for c in session_id[:48])
    if not prefix.strip("_"):
        return digest
    return f"{prefix}_{digest}"


def unloaded_segments_state_path(*, session_id: str, memory_root: str | None = None) -> Path:
    """Return JSON state file path for unloaded segments in one session."""
    if memory_root:
        root = Path(memory_root).expanduser().resolve()
    else:
        root = resolve_memory_root(mkdir=True)
    root.mkdir(parents=True, exist_ok=True)
    state_dir = root / _STATE_SUBDIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{_session_id_filename(session_id)}.json"


def _default_state() -> dict[str, Any]:
    return {"records": []}


def load_unloaded_segments_state(*, session_id: str, memory_root: str | None = None) -> dict[str, Any]:
    path = unloaded_segments_state_path(session_id=session_id, memory_root=memory_root)
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("unloaded segment state read failed: %s", exc)
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    records = raw.get("records")
    if not isinstance(records, list):
        return _default_state()
    return {"records": [r for r in records if isinstance(r, dict)]}


def save_unloaded_segments_state(
    *,
    session_id: str,
    state: dict[str, Any],
    memory_root: str | None = None,
) -> None:
    path = unloaded_segments_state_path(session_id=session_id, memory_root=memory_root)
    payload = {"records": [r for r in state.get("records", []) if isinstance(r, dict)]}
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def has_unloaded_segments(*, session_id: str, memory_root: str | None = None) -> bool:
    state = load_unloaded_segments_state(session_id=session_id, memory_root=memory_root)
    return any(not bool(r.get("restored")) for r in state.get("records", []) if isinstance(r, dict))


def append_unloaded_segment_record(
    *,
    session_id: str,
    unload_id: str,
    role: str,
    segments: list[dict[str, Any]],
    placeholder: str,
    source_summary: str,
    memory_root: str | None = None,
) -> None:
    if not unload_id or not segments:
        return
    state = load_unloaded_segments_state(session_id=session_id, memory_root=memory_root)
    records = [r for r in state.get("records", []) if isinstance(r, dict)]
    for existing in records:
        if str(existing.get("unload_id") or "") == unload_id:
            if existing.get("restored"):
                existing["restored"] = False
                existing["restored_at_ms"] = None
            existing["last_seen_at_ms"] = int(time.time() * 1000)
            save_unloaded_segments_state(session_id=session_id, state={"records": records}, memory_root=memory_root)
            return
    records.append(
        {
            "unload_id": unload_id,
            "role": role or "user",
            "segments": segments,
            "placeholder": placeholder,
            "source_summary": source_summary,
            "created_at_ms": int(time.time() * 1000),
            "last_seen_at_ms": int(time.time() * 1000),
            "restored": False,
            "restored_at_ms": None,
        }
    )
    save_unloaded_segments_state(session_id=session_id, state={"records": records}, memory_root=memory_root)


def consume_unloaded_segment_records(
    *,
    session_id: str,
    mode: str,
    unload_ids: list[str] | None = None,
    limit: int = 1,
    memory_root: str | None = None,
) -> list[dict[str, Any]]:
    state = load_unloaded_segments_state(session_id=session_id, memory_root=memory_root)
    records = [r for r in state.get("records", []) if isinstance(r, dict)]
    active = [r for r in records if not bool(r.get("restored"))]
    selected: list[dict[str, Any]] = []
    if mode == "all":
        selected = sorted(active, key=lambda r: int(r.get("created_at_ms") or 0))
    elif mode == "ids":
        wanted = {str(v).strip() for v in unload_ids or [] if str(v).strip()}
        if wanted:
            selected = [r for r in active if str(r.get("unload_id") or "") in wanted]
    else:
        ordered = sorted(active, key=lambda r: int(r.get("created_at_ms") or 0), reverse=True)
        selected = ordered[: max(1, limit)]

    if not selected:
        return []

    selected_ids = {str(r.get("unload_id") or "") for r in selected}
    now_ms = int(time.time() * 1000)
    for record in records:
        if str(record.get("unload_id") or "") in selected_ids:
            record["restored"] = True
            record["restored_at_ms"] = now_ms
    save_unloaded_segments_state(session_id=session_id, state={"records": records}, memory_root=memory_root)
    return selected


def restored_segment_fingerprints(
    *,
    session_id: str,
    memory_root: str | None = None,
) -> set[str]:
    """Return fingerprints for segments currently marked as restored."""
    state = load_unloaded_segments_state(session_id=session_id, memory_root=memory_root)
    out: set[str] = set()
    for record in state.get("records", []):
        if not isinstance(record, dict):
            continue
        if not bool(record.get("restored")):
            continue
        segments = record.get("segments")
        if not isinstance(segments, list):
            continue
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            try:
                raw = json.dumps(seg, ensure_ascii=False, sort_keys=True)
            except Exception:
                continue
            out.add(hashlib.sha256(raw.encode("utf-8")).hexdigest())
    return out

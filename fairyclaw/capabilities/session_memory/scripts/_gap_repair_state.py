# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Per-session gap-repair state (not shared MEMORY.md).

Gap repair patches context compression within one session only; state lives under
``<memory_root>/.session_gap_repair/`` so it does not mix with cross-session MEMORY.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from fairyclaw.sdk.tools import resolve_memory_root

logger = logging.getLogger(__name__)

_STATE_SUBDIR = ".session_gap_repair"


def gap_repair_state_path(*, session_id: str, memory_root: str | None) -> Path:
    """Return path to JSON state file for one session."""
    if memory_root:
        root = Path(memory_root).expanduser().resolve()
    else:
        root = resolve_memory_root(mkdir=True)
    root.mkdir(parents=True, exist_ok=True)
    state_dir = root / _STATE_SUBDIR
    state_dir.mkdir(parents=True, exist_ok=True)
    safe = _session_id_filename(session_id)
    return state_dir / f"{safe}.json"


def _session_id_filename(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    prefix = "".join(c if c.isalnum() or c in "._-" else "_" for c in session_id[:48])
    if not prefix.strip("_"):
        return digest
    return f"{prefix}_{digest}"


def load_gap_repair_state(*, session_id: str, memory_root: str | None) -> dict[str, object]:
    path = gap_repair_state_path(session_id=session_id, memory_root=memory_root)
    if not path.exists():
        return {"last_slice_exclusive_end": 0, "last_summary": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("gap repair state read failed: %s", exc)
        return {"last_slice_exclusive_end": 0, "last_summary": ""}
    if not isinstance(raw, dict):
        return {"last_slice_exclusive_end": 0, "last_summary": ""}
    end = raw.get("last_slice_exclusive_end", 0)
    summary = raw.get("last_summary", "")
    try:
        end_i = int(end)
    except (TypeError, ValueError):
        end_i = 0
    return {"last_slice_exclusive_end": max(0, end_i), "last_summary": str(summary or "")}


def save_gap_repair_state(
    *,
    session_id: str,
    memory_root: str | None,
    last_slice_exclusive_end: int,
    last_summary: str,
) -> None:
    path = gap_repair_state_path(session_id=session_id, memory_root=memory_root)
    data = {
        "last_slice_exclusive_end": int(last_slice_exclusive_end),
        "last_summary": last_summary,
    }
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

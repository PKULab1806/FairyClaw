# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Persistent state for ``session_memory_extraction`` hook (not MEMORY.md).

The after-LLM extraction hook only needs counters (messages / tokens / tool rounds /
cooldown). Those belong in ``<memory_root>/.session_memory_extraction/``, same idea as
compression hook state under ``.session_unloaded_segments/`` — not in logical MEMORY.md.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fairyclaw.sdk.tools import resolve_memory_root

logger = logging.getLogger(__name__)

_STATE_SUBDIR = ".session_memory_extraction"
_CHECKPOINT_FILE = "extraction_checkpoint.json"
# Legacy: checkpoints were appended as a line inside MEMORY.md (removed on migrate).
LEGACY_MEMORY_CHECKPOINT_PREFIX = "<!-- session_memory_checkpoint -->"
_LEGACY_MARKER = LEGACY_MEMORY_CHECKPOINT_PREFIX


def extraction_checkpoint_path(*, memory_root: str | None) -> Path:
    if memory_root:
        root = Path(memory_root).expanduser().resolve()
    else:
        root = resolve_memory_root(mkdir=True)
    root.mkdir(parents=True, exist_ok=True)
    state_dir = root / _STATE_SUBDIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / _CHECKPOINT_FILE


def _default_state() -> dict[str, int]:
    return {"since_messages": 0, "since_tokens": 0, "since_tool_rounds": 0, "cooldown": 0}


def _coerce_state(raw: dict[str, Any]) -> dict[str, int]:
    def _i(key: str) -> int:
        try:
            return int(raw.get(key, 0))
        except (TypeError, ValueError):
            return 0

    return {
        "since_messages": max(0, _i("since_messages")),
        "since_tokens": max(0, _i("since_tokens")),
        "since_tool_rounds": max(0, _i("since_tool_rounds")),
        "cooldown": max(0, _i("cooldown")),
    }


def load_extraction_checkpoint(*, memory_root: str | None) -> dict[str, int]:
    path = extraction_checkpoint_path(memory_root=memory_root)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("extraction checkpoint read failed: %s", exc)
            return _default_state()
        if isinstance(raw, dict):
            return _coerce_state(raw)
        return _default_state()
    return _default_state()


def save_extraction_checkpoint(*, memory_root: str | None, state: dict[str, int]) -> None:
    path = extraction_checkpoint_path(memory_root=memory_root)
    data = _coerce_state(state)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def migrate_legacy_checkpoint_from_memory_md(*, memory_root: str | None, memory_text: str) -> dict[str, int] | None:
    """If JSON is missing, parse last legacy MEMORY.md checkpoint line and return state, or None."""
    path = extraction_checkpoint_path(memory_root=memory_root)
    if path.exists():
        return None
    line = ""
    for row in reversed(memory_text.splitlines()):
        stripped = row.strip()
        if stripped.startswith(_LEGACY_MARKER):
            line = stripped
            break
    if not line:
        return None
    raw = line.replace(_LEGACY_MARKER, "", 1).strip()
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _coerce_state(data)


def strip_legacy_checkpoint_lines(text: str) -> str:
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(LEGACY_MEMORY_CHECKPOINT_PREFIX)]
    return "\n".join(lines).strip()

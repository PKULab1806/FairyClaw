# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import json
from pathlib import Path

from fairyclaw.capabilities.session_memory.scripts._gap_repair_state import (
    gap_repair_state_path,
    load_gap_repair_state,
    save_gap_repair_state,
)


def test_gap_repair_state_roundtrip(tmp_path: Path) -> None:
    sid = "sess_test_gap_1"
    root = str(tmp_path / "mem")
    p = gap_repair_state_path(session_id=sid, memory_root=root)
    assert p.parent.name == ".session_gap_repair"
    save_gap_repair_state(
        session_id=sid,
        memory_root=root,
        last_slice_exclusive_end=42,
        last_summary="- item",
    )
    loaded = load_gap_repair_state(session_id=sid, memory_root=root)
    assert loaded["last_slice_exclusive_end"] == 42
    assert loaded["last_summary"] == "- item"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["last_slice_exclusive_end"] == 42


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    loaded = load_gap_repair_state(session_id="sess_x", memory_root=str(tmp_path / "empty"))
    assert loaded["last_slice_exclusive_end"] == 0
    assert loaded["last_summary"] == ""

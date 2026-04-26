# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import json

from fairyclaw.capabilities.session_memory.scripts._extraction_checkpoint_state import (
    LEGACY_MEMORY_CHECKPOINT_PREFIX,
    extraction_checkpoint_path,
    load_extraction_checkpoint,
    migrate_legacy_checkpoint_from_memory_md,
    save_extraction_checkpoint,
    strip_legacy_checkpoint_lines,
)


def test_checkpoint_roundtrip(tmp_path) -> None:
    root = str(tmp_path / "mem")
    save_extraction_checkpoint(
        memory_root=root,
        state={"since_messages": 2, "since_tokens": 100, "since_tool_rounds": 1, "cooldown": 0},
    )
    path = extraction_checkpoint_path(memory_root=root)
    assert path.exists()
    got = load_extraction_checkpoint(memory_root=root)
    assert got == {"since_messages": 2, "since_tokens": 100, "since_tool_rounds": 1, "cooldown": 0}


def test_migrate_legacy_from_memory_md(tmp_path) -> None:
    root = str(tmp_path / "mem")
    legacy = (
        f"{LEGACY_MEMORY_CHECKPOINT_PREFIX} "
        + json.dumps({"since_messages": 3, "since_tokens": 1022, "since_tool_rounds": 2, "cooldown": 0})
    )
    memory = f"Some note\n{legacy}\n"
    migrated = migrate_legacy_checkpoint_from_memory_md(memory_root=root, memory_text=memory)
    assert migrated is not None
    assert migrated["since_messages"] == 3
    assert extraction_checkpoint_path(memory_root=root).exists() is False  # migrate does not write JSON
    save_extraction_checkpoint(memory_root=root, state=migrated)
    assert load_extraction_checkpoint(memory_root=root)["since_tokens"] == 1022


def test_strip_legacy_lines() -> None:
    t = f"a\n{LEGACY_MEMORY_CHECKPOINT_PREFIX} {{\"since_messages\":1}}\nb"
    stripped = strip_legacy_checkpoint_lines(t)
    assert LEGACY_MEMORY_CHECKPOINT_PREFIX not in stripped
    assert "a" in stripped
    assert "b" in stripped

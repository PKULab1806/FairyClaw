# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for env/yaml helpers in config.loader."""

from __future__ import annotations

from pathlib import Path

from fairyclaw.config.loader import merge_env_keys, read_env_file, save_yaml_atomic


def test_merge_env_keys_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "t.env"
    p.write_text("FOO=1\n", encoding="utf-8")
    merge_env_keys(p, {"BAR": "2", "FOO": "3"})
    m = read_env_file(p)
    assert m["FOO"] == "3"
    assert m["BAR"] == "2"


def test_save_yaml_atomic_writes_file(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    save_yaml_atomic(p, {"a": 1, "b": {"c": 2}})
    text = p.read_text(encoding="utf-8")
    assert "a" in text and "b" in text

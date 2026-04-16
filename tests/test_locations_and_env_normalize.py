# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for config path resolution and G8 env normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from fairyclaw.config import env_normalize, locations
from fairyclaw.config.settings import Settings


def test_resolve_config_dir_prefers_cwd_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "proj" / "config"
    cfg.mkdir(parents=True)
    monkeypatch.chdir(cfg.parent)
    monkeypatch.delenv("FAIRYCLAW_CONFIG_DIR", raising=False)
    assert locations.resolve_config_dir() == cfg.resolve()


def test_resolve_config_dir_state_root_when_no_cwd_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "h"
    state_root.mkdir()
    monkeypatch.setenv("FAIRYCLAW_HOME", str(state_root))
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("FAIRYCLAW_CONFIG_DIR", raising=False)
    expected = (state_root / "config").resolve()
    assert locations.resolve_config_dir(mkdir=False) == expected
    assert locations.resolve_config_dir(mkdir=True).is_dir()


def test_capabilities_dir_from_env_values_relative(tmp_path: Path) -> None:
    anchor = tmp_path
    v = locations.capabilities_dir_from_env_values(anchor, {"FAIRYCLAW_CAPABILITIES_DIR": "./capabilities"})
    assert v == (anchor / "capabilities").resolve()


def test_normalize_fairyclaw_env_file_writes_absolute_paths(tmp_path: Path) -> None:
    anchor = tmp_path / "root"
    anchor.mkdir()
    cfg_dir = anchor / "config"
    cfg_dir.mkdir()
    env_f = cfg_dir / "fairyclaw.env"
    env_f.write_text(
        "FAIRYCLAW_DATA_DIR=./data\n"
        "FAIRYCLAW_CAPABILITIES_DIR=./capabilities\n"
        "FAIRYCLAW_API_TOKEN=x\n",
        encoding="utf-8",
    )
    env_normalize.normalize_fairyclaw_env_file(env_f, anchor)
    text = env_f.read_text(encoding="utf-8")
    assert str((anchor / "data").resolve()) in text
    assert str((anchor / "capabilities").resolve()) in text
    assert "FAIRYCLAW_API_TOKEN=x" in text


def test_default_paths_follow_state_root_in_non_dev_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("FAIRYCLAW_HOME", str(state_root))
    monkeypatch.delenv("FAIRYCLAW_CONFIG_DIR", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    assert locations.resolve_config_dir() == (state_root / "config").resolve()
    assert locations.default_data_dir() == str((state_root / "data").resolve())
    assert locations.default_capabilities_dir() == str((state_root / "capabilities").resolve())
    assert locations.default_log_file_path() == str((state_root / "data" / "logs" / "fairyclaw.log").resolve())
    assert locations.default_database_url().startswith("sqlite+aiosqlite:////")
    assert str((state_root / "data" / "fairyclaw.db").resolve()) in locations.default_database_url()


def test_default_paths_follow_repo_anchor_in_dev_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    cfg = proj / "config"
    cfg.mkdir(parents=True)
    monkeypatch.chdir(proj)
    monkeypatch.delenv("FAIRYCLAW_CONFIG_DIR", raising=False)
    monkeypatch.delenv("FAIRYCLAW_HOME", raising=False)

    assert locations.resolve_config_dir() == cfg.resolve()
    assert locations.default_data_dir() == str((proj / "data").resolve())
    assert locations.default_capabilities_dir() == str((proj / "capabilities").resolve())


def test_settings_defaults_are_aligned_with_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("FAIRYCLAW_HOME", str(state_root))
    monkeypatch.delenv("FAIRYCLAW_CONFIG_DIR", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    s = Settings(_env_file=None)
    assert s.data_dir == str((state_root / "data").resolve())
    assert s.capabilities_dir == str((state_root / "capabilities").resolve())
    assert s.log_file_path == str((state_root / "data" / "logs" / "fairyclaw.log").resolve())
    assert "fairyclaw.db" in s.database_url

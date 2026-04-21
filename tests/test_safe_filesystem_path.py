# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Regression tests for SafeFilesystemPath containment (POSIX root /)."""

from __future__ import annotations

import os

import pytest

from fairyclaw.core.capabilities.models import SafeFilesystemPath, resolve_safe_path


@pytest.mark.skipif(os.name != "posix", reason="POSIX path layout")
def test_is_within_root_when_allowed_root_is_slash() -> None:
    sp = SafeFilesystemPath.resolve("/mnt/d/Desktop/FairyClaw", "/")
    assert sp.is_within_root()


def test_is_within_root_normal_prefix() -> None:
    sp = SafeFilesystemPath.resolve("/tmp/fairyclaw_sub/file.txt", "/tmp/fairyclaw_sub")
    assert sp.is_within_root()


def test_is_within_root_rejects_escape() -> None:
    sp = SafeFilesystemPath.resolve("/etc/passwd", "/tmp/fairyclaw_sub")
    assert not sp.is_within_root()


def test_resolve_safe_path_accepts_under_root_slash_posix() -> None:
    if os.name != "posix":
        pytest.skip("POSIX only")
    safe, err = resolve_safe_path("/mnt/d/Desktop/FairyClaw", "/")
    assert err is None
    assert safe is not None
    assert safe.is_within_root()


def test_resolve_safe_path_accepts_workspace_root_when_outside_filesystem_root(tmp_path) -> None:
    fs_root = tmp_path / "fs"
    ws_root = tmp_path / "ws"
    fs_root.mkdir(parents=True, exist_ok=True)
    ws_root.mkdir(parents=True, exist_ok=True)
    target = ws_root / "out.txt"

    safe, err = resolve_safe_path(str(target), str(fs_root), str(ws_root))
    assert err is None
    assert safe is not None
    assert safe.path == str(target.resolve())


def test_resolve_safe_path_resolves_relative_path_against_workspace(tmp_path) -> None:
    fs_root = tmp_path / "fs"
    ws_root = tmp_path / "ws"
    fs_root.mkdir(parents=True, exist_ok=True)
    ws_root.mkdir(parents=True, exist_ok=True)

    safe, err = resolve_safe_path("relative.txt", str(fs_root), str(ws_root))
    assert err is None
    assert safe is not None
    assert safe.path == str((ws_root / "relative.txt").resolve())

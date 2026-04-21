# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab

from __future__ import annotations

from pathlib import Path

import pytest

from fairyclaw.core.runtime.session_runtime_store import SessionRuntimeStore


@pytest.mark.anyio
async def test_initialize_session_uses_explicit_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAIRYCLAW_HOME", str(tmp_path / "home"))
    store = SessionRuntimeStore()
    async def _none_read(_sid: str) -> str | None:
        return None

    monkeypatch.setattr(store, "_read_workspace_from_db", _none_read)

    async def _noop_write(_sid: str, _workspace: str) -> None:
        return None

    monkeypatch.setattr(store, "_write_workspace_to_db", _noop_write)
    ctx = await store.initialize_session("sess_abcdef123456", requested_workspace_root=str(tmp_path / "my_ws"))
    assert ctx.workspace_root == str((tmp_path / "my_ws").resolve())
    assert Path(ctx.workspace_root).is_dir()
    assert ctx.short_session_id == "abcdef12"


@pytest.mark.anyio
async def test_initialize_session_builds_default_workspace_under_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("FAIRYCLAW_HOME", str(home))
    store = SessionRuntimeStore()
    async def _none_read(_sid: str) -> str | None:
        return None

    monkeypatch.setattr(store, "_read_workspace_from_db", _none_read)

    async def _noop_write(_sid: str, _workspace: str) -> None:
        return None

    monkeypatch.setattr(store, "_write_workspace_to_db", _noop_write)
    ctx = await store.initialize_session("sess_1234567890abcdef", requested_workspace_root=None)
    assert ctx.workspace_root.startswith(str((home / "workspace").resolve()))
    assert Path(ctx.workspace_root).is_dir()

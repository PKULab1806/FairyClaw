# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for benchmark CLI commands: help/send/get/session."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from fairyclaw import cli


def _stub_prepare(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "proj"
    cfg = root / "config"
    data = root / "data"
    cfg.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    return root, cfg, {"FAIRYCLAW_DATA_DIR": str(data), "FAIRYCLAW_API_TOKEN": "tok", "FAIRYCLAW_GATEWAY_PORT": "8081"}


def test_cmd_help_prints_benchmark_commands(capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    rc = cli._cmd_help(argparse.Namespace(), parser)
    assert rc == 0
    out = capsys.readouterr().out
    assert "fairyclaw help" in out
    assert "fairyclaw send <text>" in out
    assert "fairyclaw session list" in out


def test_send_named_session_create_then_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_prepare_project_config", lambda no_sync_config: _stub_prepare(tmp_path))
    calls: list[tuple[str, dict]] = []

    def fake_ws(_cfg: dict[str, str], op: str, body: dict) -> dict:
        calls.append((op, body))
        if op == "session.create":
            return {"session_id": "sess_abc"}
        if op == "chat.send":
            return {"status": "accepted", "message": "ok"}
        raise AssertionError(op)

    monkeypatch.setattr(cli, "_ws_request", fake_ws)

    rc1 = cli._cmd_send(argparse.Namespace(text=["hello"], session="bench"))
    rc2 = cli._cmd_send(argparse.Namespace(text=["world"], session="bench"))
    assert rc1 == 0
    assert rc2 == 0
    assert [op for op, _ in calls].count("session.create") == 1
    assert [op for op, _ in calls].count("chat.send") == 2


def test_send_without_session_creates_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_prepare_project_config", lambda no_sync_config: _stub_prepare(tmp_path))
    seq = {"n": 0}

    def fake_ws(_cfg: dict[str, str], op: str, _body: dict) -> dict:
        if op == "session.create":
            seq["n"] += 1
            return {"session_id": f"sess_{seq['n']}"}
        if op == "chat.send":
            return {"status": "accepted", "message": "ok"}
        raise AssertionError(op)

    monkeypatch.setattr(cli, "_ws_request", fake_ws)
    rc = cli._cmd_send(argparse.Namespace(text=["anon"], session=None))
    assert rc == 0


def test_get_supports_name_or_id_and_returns_full_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, cfg, vals = _stub_prepare(tmp_path)
    monkeypatch.setattr(cli, "_prepare_project_config", lambda no_sync_config: (root, cfg, vals))
    map_path = Path(vals["FAIRYCLAW_DATA_DIR"]) / "cli_session_map.json"
    map_path.write_text('{"bench":"sess_bench"}\n', encoding="utf-8")

    def fake_ws(_cfg: dict[str, str], op: str, body: dict) -> dict:
        assert op == "sessions.history"
        assert body["session_id"] in {"sess_bench", "sess_direct"}
        return {
            "events": [
                {"kind": "session_event", "role": "user", "text": "u1", "ts_ms": 1},
                {"kind": "session_event", "role": "assistant", "text": "a1", "ts_ms": 2},
                {"kind": "operation_event", "tool_name": "t", "result_preview": "r", "ts_ms": 3},
            ]
        }

    monkeypatch.setattr(cli, "_ws_request", fake_ws)
    assert cli._cmd_get(argparse.Namespace(target="bench")) == 0
    out1 = capsys.readouterr().out
    assert "user: u1" in out1 and "assistant: a1" in out1 and "operation:t: r" in out1

    assert cli._cmd_get(argparse.Namespace(target="sess_direct")) == 0


def test_session_list_and_rm_only_touch_local_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, cfg, vals = _stub_prepare(tmp_path)
    monkeypatch.setattr(cli, "_prepare_project_config", lambda no_sync_config: (root, cfg, vals))
    map_path = Path(vals["FAIRYCLAW_DATA_DIR"]) / "cli_session_map.json"
    map_path.write_text('{"s1":"sess_1","s2":"sess_2"}\n', encoding="utf-8")

    assert cli._cmd_session_list(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "s1\tsess_1" in out

    assert cli._cmd_session_rm(argparse.Namespace(target="s1")) == 0
    left = cli._load_cli_session_map(map_path)
    assert "s1" not in left
    assert "s2" in left


def test_send_fails_when_gateway_not_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_prepare_project_config", lambda no_sync_config: _stub_prepare(tmp_path))

    def fake_ws(_cfg: dict[str, str], _op: str, _body: dict) -> dict:
        raise RuntimeError("Gateway not reachable. 请先执行 `fairyclaw start`。")

    monkeypatch.setattr(cli, "_ws_request", fake_ws)
    with pytest.raises(RuntimeError, match="fairyclaw start"):
        cli._cmd_send(argparse.Namespace(text=["hello"], session="bench"))


def test_main_send_reports_friendly_error_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_prepare_project_config", lambda no_sync_config: _stub_prepare(tmp_path))

    def fake_ws(_cfg: dict[str, str], _op: str, _body: dict) -> dict:
        raise RuntimeError("Gateway not reachable. 请先执行 `fairyclaw start`。")

    monkeypatch.setattr(cli, "_ws_request", fake_ws)
    monkeypatch.setattr("sys.argv", ["fairyclaw", "send", "hello"])
    rc = cli.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert "Error: Gateway not reachable." in err

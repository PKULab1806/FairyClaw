from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fairyclaw.infrastructure.database.models import SessionModel
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

STATE_KEY = "code_repair_state"


def _default_state() -> dict[str, Any]:
    return {
        "phase": "reproduce",
        "verification_passed": False,
        "last_failure_signature": "",
        "produced_artifacts": [],
        "required_artifacts": [],
        "gate_fail_reasons": [],
        "last_tool_name": "",
        "last_tool_status": "",
        "last_tool_ts_ms": 0,
    }


async def load_session_meta(session_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        model = await db.get(SessionModel, session_id)
        if model is None or not isinstance(model.meta, dict):
            return {}
        return dict(model.meta)


async def load_state(session_id: str) -> dict[str, Any]:
    meta = await load_session_meta(session_id)
    raw = meta.get(STATE_KEY)
    if not isinstance(raw, dict):
        return _default_state()
    state = _default_state()
    state.update(raw)
    return state


async def save_state(session_id: str, state: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as db:
        model = await db.get(SessionModel, session_id)
        if model is None:
            return
        meta = dict(model.meta) if isinstance(model.meta, dict) else {}
        next_state = _default_state()
        next_state.update(state)
        meta[STATE_KEY] = next_state
        model.meta = meta
        await db.commit()


def resolve_workspace_root(meta: dict[str, Any], fallback: str | None = None) -> str:
    root = str(meta.get("workspace_root") or "").strip()
    if root:
        return root
    fb = str(fallback or "").strip()
    if fb:
        return fb
    return os.getcwd()


def resolve_path(workspace_root: str, raw_path: str) -> Path:
    p = Path(raw_path.strip())
    if p.is_absolute():
        return p.resolve()
    return (Path(workspace_root) / p).resolve()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def infer_required_artifacts_from_done_when(done_when: Any) -> list[str]:
    if not isinstance(done_when, list):
        return []
    outputs: list[str] = []
    for rule in done_when:
        if not isinstance(rule, dict):
            continue
        args = rule.get("args")
        if not isinstance(args, dict):
            continue
        path = args.get("path")
        if isinstance(path, str) and path.strip():
            outputs.append(path.strip())
    uniq: list[str] = []
    for p in outputs:
        if p not in uniq:
            uniq.append(p)
    return uniq


def is_protected_path(path: Path, workspace_root: str, patterns: list[str]) -> bool:
    try:
        rel = str(path.resolve().relative_to(Path(workspace_root).resolve()))
    except Exception:
        rel = str(path)
    normalized = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def ensure_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def ensure_json_array(raw: str) -> list[Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


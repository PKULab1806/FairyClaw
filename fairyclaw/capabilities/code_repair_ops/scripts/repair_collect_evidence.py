from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext
try:
    from fairyclaw_plugins.code_repair_ops.config import CodeRepairOpsRuntimeConfig
except ModuleNotFoundError:
    from fairyclaw.capabilities.code_repair_ops.config import CodeRepairOpsRuntimeConfig

from ._state import (
    file_hash,
    load_session_meta,
    load_state,
    resolve_path,
    resolve_workspace_root,
    save_state,
)


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    mode = str(args.get("mode") or "").strip().lower()
    if mode not in {"command", "log_scan", "file_snapshot"}:
        return "Error: mode must be one of command|log_scan|file_snapshot."
    cfg = expect_group_config(context, CodeRepairOpsRuntimeConfig)
    meta = await load_session_meta(context.session_id)
    state = await load_state(context.session_id)
    workspace_root = resolve_workspace_root(meta, context.workspace_root)
    capture_limit = int(args.get("capture_limit", 120) or 120)
    patterns_raw = args.get("extract_patterns")
    extract_patterns = [str(x) for x in patterns_raw] if isinstance(patterns_raw, list) else []
    targets_raw = args.get("targets")
    targets = [str(x) for x in targets_raw] if isinstance(targets_raw, list) else []

    result: dict[str, Any] = {"mode": mode, "workspace_root": workspace_root}

    if mode == "command":
        command = str(args.get("command") or "").strip()
        if not command:
            return "Error: command is required when mode=command."
        if not _is_safe_evidence_command(command, list(cfg.evidence_command_allow_prefixes)):
            return "Error: command is not allowed for repair_collect_evidence (read/test-only)."
        cwd = str(args.get("cwd") or "").strip()
        cwd_path = resolve_path(workspace_root, cwd) if cwd else Path(workspace_root)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        lines = (stdout_text + "\n" + stderr_text).splitlines()
        lines = lines[-capture_limit:]
        extracted = _extract_lines(lines, extract_patterns)
        failure_signature = extracted[0] if extracted else (lines[-1] if lines else "")
        result.update(
            {
                "command": command,
                "cwd": str(cwd_path),
                "exit_code": int(proc.returncode or 0),
                "failure_signature": failure_signature,
                "captured_lines": lines,
                "extracted_lines": extracted,
            }
        )
    elif mode == "log_scan":
        if not targets:
            return "Error: targets is required when mode=log_scan."
        scans: list[dict[str, Any]] = []
        for raw in targets:
            path = resolve_path(workspace_root, raw)
            if not path.is_file():
                scans.append({"path": str(path), "error": "not_file"})
                continue
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = content[-capture_limit:]
            extracted = _extract_lines(lines, extract_patterns)
            scans.append({"path": str(path), "extracted_lines": extracted, "tail_lines": lines[-20:]})
        result["scans"] = scans
        first = next((s for s in scans if s.get("extracted_lines")), None)
        result["failure_signature"] = str((first or {}).get("extracted_lines", [""])[0]) if first else ""
    else:
        snapshots: list[dict[str, Any]] = []
        for raw in targets:
            path = resolve_path(workspace_root, raw)
            if not path.exists():
                snapshots.append({"path": str(path), "exists": False})
                continue
            if path.is_file():
                snapshots.append({"path": str(path), "exists": True, "hash": file_hash(path), "size": path.stat().st_size})
            else:
                snapshots.append({"path": str(path), "exists": True, "type": "dir"})
        result["snapshots"] = snapshots

    prev_phase = str(state.get("phase") or "reproduce")
    if mode in {"command", "log_scan"} and prev_phase == "reproduce":
        state["phase"] = "patch"
    else:
        state["phase"] = prev_phase
    state["last_failure_signature"] = str(result.get("failure_signature") or state.get("last_failure_signature") or "")
    state["last_tool_name"] = "repair_collect_evidence"
    state["last_tool_status"] = "ok"
    await save_state(context.session_id, state)
    return json.dumps(result, ensure_ascii=False)


def _extract_lines(lines: list[str], patterns: list[str]) -> list[str]:
    if not patterns:
        patterns = ["AssertionError", "Traceback", "FAILED", "ERROR", "E   "]
    out: list[str] = []
    for line in lines:
        for pattern in patterns:
            try:
                if re.search(pattern, line):
                    out.append(line.strip())
                    break
            except re.error:
                if pattern in line:
                    out.append(line.strip())
                    break
    dedup: list[str] = []
    for line in out:
        if line not in dedup:
            dedup.append(line)
    return dedup[:20]


def _is_safe_evidence_command(command: str, allow_prefixes: list[str]) -> bool:
    normalized = command.strip()
    if not normalized:
        return False
    if allow_prefixes and not any(normalized.startswith(prefix) for prefix in allow_prefixes):
        return False
    blocked_tokens = [">", ">>", "<<", "|", "&&", ";", "tee ", "chmod ", "chown ", "rm ", "mv ", "cp ", "touch "]
    return not any(token in normalized for token in blocked_tokens)


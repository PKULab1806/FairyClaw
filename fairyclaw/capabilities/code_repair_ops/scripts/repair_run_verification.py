from __future__ import annotations

import asyncio
import json
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
    cfg = expect_group_config(context, CodeRepairOpsRuntimeConfig)
    checks = args.get("checks")
    if not isinstance(checks, list) or not checks:
        return "Error: checks must be a non-empty array."
    stop_on_fail = bool(args.get("stop_on_fail", False))
    meta = await load_session_meta(context.session_id)
    state = await load_state(context.session_id)
    workspace_root = resolve_workspace_root(meta, context.workspace_root)

    results: list[dict[str, Any]] = []
    all_passed = True
    for raw in checks:
        item = await _run_check(raw, workspace_root, list(cfg.command_allow_prefixes))
        results.append(item)
        if not bool(item.get("passed", False)):
            all_passed = False
            if stop_on_fail:
                break

    fail_reasons = [f"{r.get('name')}: {r.get('detail')}" for r in results if not bool(r.get("passed", False))]
    state["phase"] = "report" if all_passed else "patch"
    state["verification_passed"] = all_passed
    state["last_tool_name"] = "repair_run_verification"
    state["last_tool_status"] = "ok" if all_passed else "failed"
    state["gate_fail_reasons"] = [] if all_passed else ["verification_not_passed"] + fail_reasons[:6]
    await save_state(context.session_id, state)

    return json.dumps(
        {
            "ok": True,
            "all_passed": all_passed,
            "results": results,
            "fail_reasons": fail_reasons,
        },
        ensure_ascii=False,
    )


async def _run_check(raw: Any, workspace_root: str, command_allow_prefixes: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"name": "unknown", "type": "unknown", "passed": False, "detail": "check must be an object"}
    name = str(raw.get("name") or "unnamed")
    check_type = str(raw.get("type") or "")
    args = raw.get("args")
    if not isinstance(args, dict):
        # Backward-compatible fallback: allow flattened shape.
        args = {k: v for k, v in raw.items() if k not in {"name", "type"}}
    if not isinstance(args, dict):
        return {"name": name, "type": check_type, "passed": False, "detail": "args must be object"}
    if check_type == "command":
        command = str(args.get("command") or "").strip()
        expected = int(args.get("exit_code", 0))
        if not command:
            return {"name": name, "type": check_type, "passed": False, "detail": "command is required"}
        if command_allow_prefixes and not any(command.startswith(prefix) for prefix in command_allow_prefixes):
            return {
                "name": name,
                "type": check_type,
                "passed": False,
                "detail": "command not allowed by policy",
            }
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace_root,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        return {
            "name": name,
            "type": check_type,
            "passed": rc == expected,
            "detail": f"exit_code={rc}, expected={expected}",
            "exit_code": rc,
        }
    if check_type == "file_hash_unchanged":
        file_path = str(args.get("file_path") or "").strip()
        expected_hash = str(args.get("expected_hash") or "").strip()
        if not file_path or not expected_hash:
            return {"name": name, "type": check_type, "passed": False, "detail": "file_path and expected_hash required"}
        target = resolve_path(workspace_root, file_path)
        if not target.is_file():
            return {"name": name, "type": check_type, "passed": False, "detail": f"not file: {target}"}
        got = file_hash(target)
        return {
            "name": name,
            "type": check_type,
            "passed": got == expected_hash,
            "detail": f"hash={got}",
        }
    if check_type == "text_contains":
        file_path = str(args.get("file_path") or "").strip()
        terms = args.get("terms")
        if not file_path or not isinstance(terms, list):
            return {"name": name, "type": check_type, "passed": False, "detail": "file_path and terms[] required"}
        target = resolve_path(workspace_root, file_path)
        if not target.is_file():
            return {"name": name, "type": check_type, "passed": False, "detail": f"not file: {target}"}
        text = target.read_text(encoding="utf-8", errors="replace")
        missed = [str(t) for t in terms if str(t) not in text]
        return {
            "name": name,
            "type": check_type,
            "passed": not missed,
            "detail": "ok" if not missed else f"missing terms: {missed}",
        }
    if check_type == "text_first_line_startswith":
        file_path = str(args.get("file_path") or "").strip()
        prefix = str(args.get("prefix") or "").strip()
        if not file_path or not prefix:
            return {"name": name, "type": check_type, "passed": False, "detail": "file_path and prefix required"}
        target = resolve_path(workspace_root, file_path)
        if not target.is_file():
            return {"name": name, "type": check_type, "passed": False, "detail": f"not file: {target}"}
        first_non_empty = ""
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                first_non_empty = line.strip()
                break
        passed = first_non_empty.startswith(prefix)
        return {
            "name": name,
            "type": check_type,
            "passed": passed,
            "detail": f"first_line={first_non_empty!r}",
        }
    if check_type == "git_ancestor":
        ancestor = str(args.get("ancestor") or "").strip()
        descendant = str(args.get("descendant") or "").strip()
        if not ancestor or not descendant:
            return {"name": name, "type": check_type, "passed": False, "detail": "ancestor and descendant required"}
        cmd = f"git merge-base --is-ancestor {ancestor} {descendant}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=workspace_root,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        return {
            "name": name,
            "type": check_type,
            "passed": rc == 0,
            "detail": "ok" if rc == 0 else f"merge-base rc={rc}",
        }
    return {"name": name, "type": check_type, "passed": False, "detail": f"unsupported check type: {check_type}"}


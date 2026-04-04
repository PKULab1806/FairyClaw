# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tool-call logging helpers for planner observability."""

from __future__ import annotations

import json
from typing import Any


def make_short_tool_call_id(raw_call_id: str, index: int) -> str:
    """Generate compact stable tool-call ID for stored operation events."""
    normalized = str(raw_call_id or "").strip()
    suffix = normalized[-6:] if normalized else f"{index + 1}"
    return f"tc_{index + 1}_{suffix}"


def truncate_for_log(value: str, limit: int = 300) -> str:
    """Truncate long strings for safe log readability."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(truncated, len={len(text)})"


def summarize_tool_args(tool_name: str, args_json: str) -> str:
    """Build compact tool-argument summary for logs."""
    parsed: Any = None
    try:
        parsed = json.loads(args_json or "{}")
    except Exception:
        return truncate_for_log(args_json)
    if not isinstance(parsed, dict):
        return truncate_for_log(json.dumps(parsed, ensure_ascii=False))
    if tool_name == "run_command":
        command = parsed.get("command")
        cwd = parsed.get("cwd")
        command_type = parsed.get("command_type")
        blocking = parsed.get("blocking")
        return (
            f"command={truncate_for_log(str(command), 500)}; "
            f"cwd={truncate_for_log(str(cwd), 120)}; "
            f"command_type={command_type}; blocking={blocking}"
        )
    if tool_name == "delegate_task":
        instruction = parsed.get("instruction")
        task_type = parsed.get("task_type")
        selected_groups = parsed.get("selected_groups")
        return (
            f"instruction={truncate_for_log(str(instruction), 300)}; "
            f"task_type={task_type}; selected_groups={truncate_for_log(str(selected_groups), 180)}"
        )
    compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return truncate_for_log(compact, 500)

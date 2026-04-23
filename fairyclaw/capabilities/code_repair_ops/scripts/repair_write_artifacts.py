from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fairyclaw.sdk.tools import ToolContext

from ._state import (
    load_session_meta,
    load_state,
    resolve_path,
    resolve_workspace_root,
    save_state,
)


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    artifact_type = str(args.get("artifact_type") or "").strip()
    output_path = str(args.get("output_path") or "").strip()
    if not artifact_type or not output_path:
        return "Error: artifact_type and output_path are required."
    content = str(args.get("content") or "")

    meta = await load_session_meta(context.session_id)
    state = await load_state(context.session_id)
    workspace_root = resolve_workspace_root(meta, context.workspace_root)
    path = resolve_path(workspace_root, output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _render_content(artifact_type, content, state)
    path.write_text(body, encoding="utf-8")
    produced = list(state.get("produced_artifacts") or [])
    rel = str(path)
    if rel not in produced:
        produced.append(rel)
    state["produced_artifacts"] = produced
    state["phase"] = "report"
    state["last_tool_name"] = "repair_write_artifacts"
    state["last_tool_status"] = "ok"
    await save_state(context.session_id, state)
    return json.dumps(
        {
            "ok": True,
            "output_path": str(path),
            "artifact_type": artifact_type,
        },
        ensure_ascii=False,
    )


def _render_content(artifact_type: str, content: str, state: dict[str, Any]) -> str:
    if artifact_type == "progress_md":
        steps_lines = ["- Reproduce failure", "- Apply patch", "- Re-run verification"]
        return (
            "# Progress Report\n\n"
            "## Task\ncode_repair\n\n"
            f"## Root Cause\n{state.get('last_failure_signature', '')}\n\n"
            "## Steps\n"
            + "\n".join(steps_lines)
            + "\n\n## Verification\n"
            f"{'passed' if state.get('verification_passed') else 'failed'}\n"
        )
    if artifact_type == "review_txt":
        decision = "APPROVE"
        reason = content
        return f"{decision}\n{reason}\n"
    if artifact_type == "rca_md":
        return (
            "# RCA Report\n\n"
            f"## Symptom\n{content}\n\n"
            f"## Root Cause\n{state.get('last_failure_signature', '')}\n\n"
            "## Fix\nApplied repair patch.\n\n"
            f"## Verification\n{'passed' if state.get('verification_passed') else 'failed'}\n"
        )
    if artifact_type == "runtime_summary_json":
        return json.dumps({"repair_state": state}, ensure_ascii=False, indent=2) + "\n"
    return content


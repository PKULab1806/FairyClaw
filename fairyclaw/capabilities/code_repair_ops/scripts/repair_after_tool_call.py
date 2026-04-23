from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from fairyclaw.sdk.hooks import (
    AfterToolCallHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
)
from fairyclaw.sdk.runtime import publish_runtime_event
from fairyclaw.core.events.bus import EventType

from ._state import load_session_meta, load_state, resolve_path, resolve_workspace_root, save_state


async def execute_hook(
    hook_input: HookStageInput[AfterToolCallHookPayload],
) -> HookStageOutput[AfterToolCallHookPayload]:
    payload = hook_input.payload
    if not hook_input.context.is_sub_session:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    state = await load_state(payload.session_id)

    state["last_tool_name"] = payload.request.name
    state["last_tool_status"] = payload.tool_status
    state["last_tool_ts_ms"] = int(time.time() * 1000)
    if payload.request.name == "repair_apply_unified_patch":
        state["phase"] = "verify"
    elif payload.request.name == "repair_run_verification":
        state["phase"] = "report" if bool(state.get("verification_passed")) else "patch"
    elif payload.request.name == "repair_write_artifacts":
        state["phase"] = "report"

    passed, reasons = await _done_gate(payload.session_id, state)
    state["gate_fail_reasons"] = reasons
    await save_state(payload.session_id, state)
    if passed:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    # Force a follow-up turn with explicit guidance.
    await publish_runtime_event(
        EventType.USER_MESSAGE_RECEIVED,
        payload.session_id,
        payload={
            "trigger_turn": True,
            "task_type": hook_input.context.task_type or "code",
            "enabled_groups": list(payload.enabled_groups or []),
            "internal_user_text": "CodeRepair gate pending: " + "; ".join(reasons[:8]),
        },
        source="code_repair_ops_after_tool_call",
    )
    patched = replace(
        payload,
        force_finish=True,
        force_finish_reason="code_repair_followup_required",
    )
    return HookStageOutput(status=HookStatus.OK, patched_payload=patched)


async def _done_gate(session_id: str, state: dict[str, object]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    verification_passed = bool(state.get("verification_passed"))
    if not verification_passed:
        reasons.append("verification_not_passed")
    required = list(state.get("required_artifacts") or [])
    meta = await load_session_meta(session_id)
    workspace_root = resolve_workspace_root(meta, "")
    # Only gate artifacts after verification succeeds (report stage).
    if verification_passed:
        for raw in required:
            p = resolve_path(workspace_root, str(raw))
            if not p.exists():
                reasons.append(f"artifact_missing:{raw}")
    return (len(reasons) == 0), reasons


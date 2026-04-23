from __future__ import annotations

from dataclasses import replace

from fairyclaw.sdk.hooks import (
    BeforeLlmCallHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
    LlmChatMessage,
)

from ._state import (
    infer_required_artifacts_from_done_when,
    load_session_meta,
    load_state,
    save_state,
)


def _allowed_tools_for_phase(phase: str) -> set[str]:
    if phase == "reproduce":
        return {"repair_collect_evidence"}
    if phase == "diagnose":
        return {"repair_collect_evidence", "repair_apply_unified_patch"}
    if phase == "patch":
        return {"repair_collect_evidence", "repair_apply_unified_patch"}
    if phase == "verify":
        return {"repair_run_verification"}
    if phase == "report":
        return {"repair_write_artifacts", "report_subtask_done"}
    return {"repair_collect_evidence"}


async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    payload = hook_input.payload
    if not hook_input.context.is_sub_session:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    meta = await load_session_meta(payload.turn.session_id)
    state = await load_state(payload.turn.session_id)
    if not state.get("required_artifacts"):
        state["required_artifacts"] = infer_required_artifacts_from_done_when(meta.get("done_when"))
    await save_state(payload.turn.session_id, state)

    phase = str(state.get("phase") or "reproduce")
    allowed = _allowed_tools_for_phase(phase)
    filtered_tools = [tool for tool in payload.tools if tool.name in allowed]
    # Keep safety fallback so model can still continue if filtering becomes empty.
    if not filtered_tools:
        filtered_tools = payload.tools

    gate_reasons = list(state.get("gate_fail_reasons") or [])
    guidance = [
        "[CodeRepairOpsWorkflow]",
        f"- Current phase: {phase}",
        f"- Allowed tools this turn: {', '.join(sorted([t.name for t in filtered_tools]))}",
        "- Follow workflow strictly: reproduce -> patch -> verify -> report.",
        "- Do not claim completion without verification evidence.",
        "- If required artifact files are configured, make sure they exist before finishing.",
        "- Use patching only via repair_apply_unified_patch.",
        "- For repair_run_verification, each check must be {name, type, args}.",
    ]
    if gate_reasons:
        guidance.append(f"- Must resolve gate failures first: {gate_reasons[:6]}")
    guidance_text = "\n".join(guidance)
    patched_turn = replace(
        payload.turn,
        llm_messages=[*payload.turn.llm_messages, LlmChatMessage(role="user", content=guidance_text)],
    )
    patched_payload = replace(payload, turn=patched_turn, tools=filtered_tools)
    return HookStageOutput(status=HookStatus.OK, patched_payload=patched_payload)


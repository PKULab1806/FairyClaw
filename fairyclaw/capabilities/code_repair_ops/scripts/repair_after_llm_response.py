from __future__ import annotations

import json
from dataclasses import replace

from fairyclaw.sdk.hooks import (
    AfterLlmResponseHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
    LlmToolCallRequest,
)
from fairyclaw.sdk.runtime import publish_runtime_event
from fairyclaw.core.events.bus import EventType

from ._state import load_state


async def execute_hook(
    hook_input: HookStageInput[AfterLlmResponseHookPayload],
) -> HookStageOutput[AfterLlmResponseHookPayload]:
    payload = hook_input.payload
    if not hook_input.context.is_sub_session:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    state = await load_state(payload.session_id)
    if payload.tool_calls:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    phase = str(state.get("phase") or "reproduce")
    forced_call: LlmToolCallRequest | None = None
    if phase == "reproduce":
        forced_call = LlmToolCallRequest(
            call_id="repair_force_collect",
            name="repair_collect_evidence",
            arguments_json=json.dumps(
                {"mode": "command", "command": "pytest fixtures/app/test_config.py -q"},
                ensure_ascii=False,
            ),
        )
    elif phase == "verify":
        forced_call = LlmToolCallRequest(
            call_id="repair_force_verify",
            name="repair_run_verification",
            arguments_json=json.dumps(
                {
                    "checks": [
                        {
                            "name": "pytest_target",
                            "type": "command",
                            "args": {"command": "pytest fixtures/app/test_config.py -q", "exit_code": 0},
                        }
                    ],
                    "stop_on_fail": False,
                },
                ensure_ascii=False,
            ),
        )
    elif phase == "report":
        reasons = list(state.get("gate_fail_reasons") or [])
        if reasons:
            await publish_runtime_event(
                EventType.USER_MESSAGE_RECEIVED,
                payload.session_id,
                payload={
                    "trigger_turn": True,
                    "task_type": payload.task_type or "code",
                    "enabled_groups": list(payload.enabled_groups or []),
                    "internal_user_text": (
                        "CodeRepair gate not satisfied. Resolve these first: "
                        + "; ".join(str(x) for x in reasons[:6])
                    ),
                },
                source="code_repair_ops_after_llm_response",
            )
            patched = replace(
                payload,
                force_finish=True,
                force_finish_reason="code_repair_gate_not_satisfied",
            )
            return HookStageOutput(status=HookStatus.OK, patched_payload=patched)

    if forced_call is None:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    patched_payload = replace(payload, tool_calls=[forced_call])
    return HookStageOutput(status=HookStatus.OK, patched_payload=patched_payload)


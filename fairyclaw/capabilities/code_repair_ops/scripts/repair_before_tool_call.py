from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fairyclaw.sdk.hooks import (
    BeforeToolCallHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
    LlmToolCallRequest,
)
try:
    from fairyclaw_plugins.code_repair_ops.config import CodeRepairOpsRuntimeConfig
except ModuleNotFoundError:
    from fairyclaw.capabilities.code_repair_ops.config import CodeRepairOpsRuntimeConfig

from ._state import (
    ensure_json_object,
    is_protected_path,
    load_session_meta,
    resolve_path,
    resolve_workspace_root,
)


async def execute_hook(
    hook_input: HookStageInput[BeforeToolCallHookPayload],
) -> HookStageOutput[BeforeToolCallHookPayload]:
    payload = hook_input.payload
    if not hook_input.context.is_sub_session:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    name = payload.request.name
    if name not in {
        "repair_collect_evidence",
        "repair_run_verification",
        "repair_write_artifacts",
        "repair_apply_unified_patch",
    }:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    args = ensure_json_object(payload.request.arguments_json)
    cfg = CodeRepairOpsRuntimeConfig()
    meta = await load_session_meta(payload.session_id)
    workspace_root = resolve_workspace_root(meta, "")

    if name == "repair_apply_unified_patch":
        file_path = str(args.get("file_path") or "").strip()
        if file_path:
            target = resolve_path(workspace_root, file_path)
            if is_protected_path(target, workspace_root, list(cfg.protected_file_globs)):
                return HookStageOutput(
                    status=HookStatus.OK,
                    patched_payload=replace(
                        payload,
                        request=LlmToolCallRequest(
                            call_id=payload.request.call_id,
                            name="repair_write_artifacts",
                            arguments_json=json.dumps(
                                {
                                    "artifact_type": "custom_text",
                                    "output_path": "progress.md",
                                    "content": (
                                        f"[CodeRepair Guard] blocked edit on protected path: {target}."
                                        " Continue by editing allowed source files."
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    ),
                )
            if not target.exists():
                return HookStageOutput(
                    status=HookStatus.OK,
                    patched_payload=replace(
                        payload,
                        force_finish=True,
                        force_finish_reason="unified_patch_target_not_found_use_write_artifacts",
                    ),
                )
        patch_text = str(args.get("patch_text") or "")
        if "@@" not in patch_text:
            return HookStageOutput(
                status=HookStatus.OK,
                patched_payload=replace(
                    payload,
                    force_finish=True,
                    force_finish_reason="unified_patch_requires_context_hunk",
                ),
            )

    if name == "repair_run_verification":
        checks = args.get("checks")
        if not isinstance(checks, list) or not checks:
            return HookStageOutput(
                status=HookStatus.OK,
                patched_payload=replace(
                    payload,
                    force_finish=True,
                    force_finish_reason="verification_checks_missing",
                ),
            )
    return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)


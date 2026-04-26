#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Hide compression recovery tool when there is nothing to reload."""

from __future__ import annotations

from dataclasses import replace

from fairyclaw.capabilities.compression_hooks.scripts._unloaded_segments_state import has_unloaded_segments
from fairyclaw.sdk.hooks import (
    HookStageInput,
    HookStageOutput,
    HookStatus,
    ToolsPreparedHookPayload,
)

RELOAD_TOOL = "reload_unloaded_segments"


async def execute_hook(
    hook_input: HookStageInput[ToolsPreparedHookPayload],
) -> HookStageOutput[ToolsPreparedHookPayload]:
    """Hide reload tool when current session has no unloaded segments."""
    payload = hook_input.payload
    available = has_unloaded_segments(session_id=payload.session_id)
    if available:
        return HookStageOutput(
            status=HookStatus.SKIP,
            patched_payload=payload,
            artifacts={"has_unloaded_segments": True},
        )

    filtered = [tool for tool in payload.tools if tool.name != RELOAD_TOOL]
    if len(filtered) == len(payload.tools):
        return HookStageOutput(
            status=HookStatus.SKIP,
            patched_payload=payload,
            artifacts={"has_unloaded_segments": False},
        )
    return HookStageOutput(
        status=HookStatus.OK,
        patched_payload=replace(payload, tools=filtered),
        artifacts={"has_unloaded_segments": False},
    )

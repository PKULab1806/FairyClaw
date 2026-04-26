# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Hide image-generation tool when configured endpoint profile is unavailable."""

from __future__ import annotations

import os
from dataclasses import replace

from fairyclaw.infrastructure.llm.config import load_llm_endpoint_config
from fairyclaw.sdk.hooks import (
    HookStageInput,
    HookStageOutput,
    HookStatus,
    ToolsPreparedHookPayload,
)

DEFAULT_IMAGE_PROFILE = "image_generation"
TARGET_TOOL = "generate_or_edit_image"


async def execute_hook(
    hook_input: HookStageInput[ToolsPreparedHookPayload],
) -> HookStageOutput[ToolsPreparedHookPayload]:
    """Remove generate_or_edit_image from tool list when profile is not ready."""
    payload = hook_input.payload
    cfg = load_llm_endpoint_config()
    profile = cfg.profiles.get(DEFAULT_IMAGE_PROFILE)

    available = False
    if profile is not None:
        api_key = os.getenv(profile.api_key_env, "").strip()
        available = bool(api_key) and profile.profile_type in {"chat", "image_generation"}

    if available:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    filtered = [tool for tool in payload.tools if tool.name != TARGET_TOOL]
    patched = replace(payload, tools=filtered)
    return HookStageOutput(
        status=HookStatus.OK,
        patched_payload=patched,
        artifacts={"image_generation_available": False},
    )

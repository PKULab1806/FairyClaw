# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Default routing hook."""

from __future__ import annotations

import logging

from fairyclaw.sdk.hooks import (
    AfterLlmResponseHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
)

logger = logging.getLogger(__name__)


async def execute_hook(
    hook_input: HookStageInput[AfterLlmResponseHookPayload],
) -> HookStageOutput[AfterLlmResponseHookPayload]:
    """No-op after-LLM-response hook placeholder."""
    payload = hook_input.payload
    current = payload.enabled_groups
    logger.debug("turn_routing noop hook executed: session_id=%s", hook_input.context.session_id)
    return HookStageOutput(
        status=HookStatus.SKIP,
        artifacts={"route_hints": {"enabled_groups": current}},
        patched_payload=payload,
    )


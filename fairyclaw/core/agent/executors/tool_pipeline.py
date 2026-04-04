# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tool execution pipeline based on planner orchestration."""

from __future__ import annotations

from fairyclaw.infrastructure.llm.client import ChatResult

from fairyclaw.core.agent.hooks.hook_stage_runner import HookStageRunner
from fairyclaw.core.agent.hooks.protocol import (
    AfterLlmResponseHookPayload,
    HookExecutionContext,
    HookStage,
    LlmToolCallRequest,
)


class ToolPipelineExecutor:
    """Execute after-LLM hook stage before per-tool execution."""

    async def run_after_llm_response(
        self,
        stage_runner: HookStageRunner,
        hook_context: HookExecutionContext,
        enabled_groups: list[str],
        llm_response: ChatResult,
        tool_calls: list[LlmToolCallRequest],
    ) -> AfterLlmResponseHookPayload:
        """Run `after_llm_response` stage and return patched payload."""
        stage_payload = AfterLlmResponseHookPayload(
            session_id=hook_context.session_id,
            task_type=hook_context.task_type,
            is_sub_session=hook_context.is_sub_session,
            enabled_groups=list(enabled_groups),
            message_text=llm_response.text or None,
            tool_calls=tool_calls,
            raw_llm_result=llm_response,
        )
        stage_output = await stage_runner.run_stage(
            stage=HookStage.AFTER_LLM_RESPONSE,
            hook_context=hook_context,
            payload=stage_payload,
            enabled_groups=enabled_groups,
        )
        if isinstance(stage_output.patched_payload, AfterLlmResponseHookPayload):
            return stage_output.patched_payload
        return stage_payload

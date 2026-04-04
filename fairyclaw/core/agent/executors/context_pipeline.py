# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Context building pipeline based on hook stages."""

from __future__ import annotations

from collections.abc import Callable

from fairyclaw.config import settings
from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, UserTurn
from fairyclaw.core.agent.hooks.hook_stage_runner import HookStageRunner
from fairyclaw.core.agent.hooks.protocol import (
    BeforeLlmCallHookPayload,
    HookExecutionContext,
    HookStage,
    LlmChatMessage,
    LlmFunctionToolSpec,
    LlmTurnContext,
)
from fairyclaw.core.capabilities.models import CapabilityGroup


class ContextPipelineExecutor:
    """Execute `before_llm_call` stage for turn context payload."""

    async def run(
        self,
        stage_runner: HookStageRunner,
        turn_id_factory: Callable[[], str],
        always_enabled_groups: list[str],
        registry_groups: dict[str, CapabilityGroup],
        session_id: str,
        messages: list[LlmChatMessage],
        tools: list[LlmFunctionToolSpec],
        history_items: list[ChatHistoryItem],
        user_turn: UserTurn | None,
        enabled_groups: list[str],
        task_type: str,
        is_sub_session: bool,
    ) -> tuple[BeforeLlmCallHookPayload, HookExecutionContext]:
        """Run context stage and return transformed before-LLM payload."""
        hook_context = HookExecutionContext(
            session_id=session_id,
            turn_id=turn_id_factory(),
            task_type=task_type,
            is_sub_session=is_sub_session,
            enabled_groups=list(enabled_groups),
            always_enabled_groups=always_enabled_groups or [
                name
                for name, group in registry_groups.items()
                if (group.always_enable_subagent if is_sub_session else group.always_enable_planner)
            ],
            token_budget=getattr(settings, "context_token_budget", None),
        )

        stage_payload = BeforeLlmCallHookPayload(
            turn=LlmTurnContext(
                llm_messages=messages,
                history_items=history_items,
                user_turn=user_turn,
                session_id=session_id,
                task_type=task_type,
                is_sub_session=is_sub_session,
            ),
            tools=tools,
            token_budget=hook_context.token_budget,
        )
        stage_output = await stage_runner.run_stage(
            stage=HookStage.BEFORE_LLM_CALL,
            hook_context=hook_context,
            payload=stage_payload,
            enabled_groups=enabled_groups,
        )
        if isinstance(stage_output.patched_payload, BeforeLlmCallHookPayload):
            stage_payload = stage_output.patched_payload
        return stage_payload, hook_context

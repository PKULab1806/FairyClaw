# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""SDK re-exports: hook protocol types and stage payload contracts."""

from fairyclaw.core.agent.hooks.protocol import (
    AfterLlmResponseHookPayload,
    AfterToolCallHookPayload,
    BeforeLlmCallHookPayload,
    BeforeToolCallHookPayload,
    EventHookHandler,
    HookError,
    HookExecutionContext,
    HookStage,
    HookStageInput,
    HookStageOutput,
    HookStatus,
    JsonObject,
    JsonPrimitive,
    JsonValue,
    LlmChatMessage,
    LlmFunctionToolSpec,
    LlmToolCallRequest,
    LlmTurnContext,
    ToolsPreparedHookPayload,
)

__all__ = [
    "AfterLlmResponseHookPayload",
    "AfterToolCallHookPayload",
    "BeforeLlmCallHookPayload",
    "BeforeToolCallHookPayload",
    "EventHookHandler",
    "HookError",
    "HookExecutionContext",
    "HookStage",
    "HookStageInput",
    "HookStageOutput",
    "HookStatus",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "LlmChatMessage",
    "LlmFunctionToolSpec",
    "LlmToolCallRequest",
    "LlmTurnContext",
    "ToolsPreparedHookPayload",
]

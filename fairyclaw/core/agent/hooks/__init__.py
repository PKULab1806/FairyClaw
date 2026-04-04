# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Hook protocol and runtime exports."""

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
    JsonValue,
    LlmChatMessage,
    LlmFunctionToolSpec,
    LlmToolCallRequest,
    LlmTurnContext,
    ToolsPreparedHookPayload,
    to_openai_messages,
)
from fairyclaw.core.agent.hooks.runtime import HookRuntime

__all__ = [
    "HookError",
    "HookExecutionContext",
    "HookStage",
    "HookStageInput",
    "HookStageOutput",
    "HookStatus",
    "JsonValue",
    "JsonObject",
    "LlmFunctionToolSpec",
    "LlmToolCallRequest",
    "LlmChatMessage",
    "LlmTurnContext",
    "ToolsPreparedHookPayload",
    "BeforeLlmCallHookPayload",
    "AfterLlmResponseHookPayload",
    "BeforeToolCallHookPayload",
    "AfterToolCallHookPayload",
    "EventHookHandler",
    "to_openai_messages",
    "HookRuntime",
]

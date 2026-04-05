# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""FairyClaw capability SDK.

Stable import surface for capability group scripts.  Import from sub-modules:

    from fairyclaw.sdk.tools import ToolContext, resolve_safe_path
    from fairyclaw.sdk.ir import SessionMessageBlock, ToolCallRound
    from fairyclaw.sdk.hooks import BeforeLlmCallHookPayload, HookStatus
    from fairyclaw.sdk.runtime import publish_user_message_received, request_planner_wakeup
    from fairyclaw.sdk.subtasks import bind_sub_session, is_sub_session_cancel_requested
    from fairyclaw.sdk.events import EventType, FileUploadReceivedEventPayload
    from fairyclaw.sdk.types import ContentSegment, SystemPromptPart, SUB_SESSION_MARKER
    from fairyclaw.sdk.group_runtime import load_group_runtime_config, expect_group_config

Do NOT import the global ``settings`` singleton from this package.
"""

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

import fairyclaw.core.agent.planning.planner as planner_module
from fairyclaw.core.agent.hooks.protocol import (
    AfterLlmResponseHookPayload,
    AfterToolCallHookPayload,
    BeforeLlmCallHookPayload,
    BeforeToolCallHookPayload,
    HookExecutionContext,
    HookStage,
    HookStageOutput,
    HookStatus,
    LlmChatMessage,
    LlmToolCallRequest,
    LlmTurnContext,
)
from fairyclaw.core.agent.planning.planner import Planner
from fairyclaw.core.agent.types import TurnRequest, TurnRuntimePrefs
from fairyclaw.core.events.bus import EventType
from fairyclaw.infrastructure.llm.client import ChatResult, ToolCall


class StubClient:
    def __init__(self, result: ChatResult) -> None:
        self.result = result
        self.called = False

    async def chat_with_tools(self, messages, tools):
        self.called = True
        return self.result


class SequencedStubClient:
    def __init__(self, results: list[ChatResult]) -> None:
        self.results = list(results)
        self.called = 0

    async def chat_with_tools(self, messages, tools):
        self.called += 1
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def _make_turn_context(session_id: str = "sess_force") -> LlmTurnContext:
    return LlmTurnContext(
        llm_messages=[LlmChatMessage(role="user", content="hello")],
        history_items=[],
        user_turn=None,
        session_id=session_id,
        task_type="general",
        is_sub_session=False,
    )


def _make_hook_context(session_id: str = "sess_force") -> HookExecutionContext:
    return HookExecutionContext(
        session_id=session_id,
        turn_id="turn_force",
        task_type="general",
        is_sub_session=False,
        enabled_groups=["CoreOperations"],
    )


def _build_planner() -> Planner:
    planner = Planner()
    planner._resolve_tools_for_session = lambda session_id, selected_groups=None: ["CoreOperations"]
    planner._build_tool_specs = lambda enabled_groups: []
    planner.context_builder.build = lambda history_items, user_segments, session_id, task_type, workspace_root=None: (
        [LlmChatMessage(role="user", content="hello")],
        [],
        None,
    )
    return planner


def test_before_llm_force_finish_skips_llm_and_follow_up() -> None:
    planner = _build_planner()
    stub_client = StubClient(ChatResult(text="", tool_calls=[]))
    published: list[tuple[EventType, dict[str, object]]] = []
    original_publish = planner_module.publish_runtime_event

    async def fake_publish_runtime_event(event_type, session_id, payload=None, source="runtime") -> bool:
        published.append((event_type, payload or {}))
        return True

    async def fake_run_stage(stage, hook_context, payload, enabled_groups=None):
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async def fake_context_pipeline_run(**kwargs):
        hook_context = _make_hook_context(kwargs["session_id"])
        return (
            BeforeLlmCallHookPayload(
                turn=_make_turn_context(kwargs["session_id"]),
                tools=[],
                token_budget=None,
                force_finish=True,
                force_finish_reason="hook_stop_before_llm",
            ),
            hook_context,
        )

    planner.resolve_llm_client = lambda task_type: stub_client
    planner.hook_stage_runner.run_stage = fake_run_stage
    planner.context_pipeline.run = fake_context_pipeline_run
    planner_module.publish_runtime_event = fake_publish_runtime_event
    try:
        asyncio.run(
            planner.process_turn(
                TurnRequest(session_id="sess_force", user_segments=(), runtime=TurnRuntimePrefs(task_type="general"))
            )
        )
    finally:
        planner_module.publish_runtime_event = original_publish

    assert stub_client.called is False
    assert [event_type for event_type, _ in published] == [EventType.FORCE_FINISH_REQUESTED]


def test_after_llm_force_finish_skips_tool_execution_and_follow_up() -> None:
    planner = _build_planner()
    stub_client = StubClient(
        ChatResult(
            text="",
            tool_calls=[ToolCall(id="tc_1", name="run_command", arguments='{"command":"pwd"}')],
        )
    )
    published: list[tuple[EventType, dict[str, object]]] = []
    executed = False
    original_publish = planner_module.publish_runtime_event

    async def fake_publish_runtime_event(event_type, session_id, payload=None, source="runtime") -> bool:
        published.append((event_type, payload or {}))
        return True

    async def fake_run_stage(stage, hook_context, payload, enabled_groups=None):
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async def fake_context_pipeline_run(**kwargs):
        hook_context = _make_hook_context(kwargs["session_id"])
        return (
            BeforeLlmCallHookPayload(
                turn=_make_turn_context(kwargs["session_id"]),
                tools=[],
                token_budget=None,
            ),
            hook_context,
        )

    async def fake_after_llm_response(**kwargs):
        return AfterLlmResponseHookPayload(
            session_id=kwargs["hook_context"].session_id,
            task_type=kwargs["hook_context"].task_type,
            is_sub_session=False,
            enabled_groups=["CoreOperations"],
            message_text=None,
            tool_calls=[LlmToolCallRequest(call_id="tc_1", name="run_command", arguments_json='{"command":"pwd"}')],
            raw_llm_result=kwargs["llm_response"],
            force_finish=True,
            force_finish_reason="hook_stop_after_llm",
        )

    async def fake_execute(**kwargs):
        nonlocal executed
        executed = True
        return "ok"

    planner.resolve_llm_client = lambda task_type: stub_client
    planner.hook_stage_runner.run_stage = fake_run_stage
    planner.context_pipeline.run = fake_context_pipeline_run
    planner.tool_pipeline.run_after_llm_response = fake_after_llm_response
    planner.tool_runtime.execute = fake_execute
    planner_module.publish_runtime_event = fake_publish_runtime_event
    try:
        asyncio.run(
            planner.process_turn(
                TurnRequest(session_id="sess_force", user_segments=(), runtime=TurnRuntimePrefs(task_type="general"))
            )
        )
    finally:
        planner_module.publish_runtime_event = original_publish

    assert stub_client.called is True
    assert executed is False
    assert [event_type for event_type, _ in published] == [EventType.FORCE_FINISH_REQUESTED]


def test_before_tool_call_force_finish_skips_tool_and_follow_up() -> None:
    planner = _build_planner()
    stub_client = StubClient(
        ChatResult(
            text="",
            tool_calls=[ToolCall(id="tc_1", name="run_command", arguments='{"command":"pwd"}')],
        )
    )
    published: list[tuple[EventType, dict[str, object]]] = []
    executed = False
    original_publish = planner_module.publish_runtime_event

    async def fake_publish_runtime_event(event_type, session_id, payload=None, source="runtime") -> bool:
        published.append((event_type, payload or {}))
        return True

    async def fake_context_pipeline_run(**kwargs):
        hook_context = _make_hook_context(kwargs["session_id"])
        return (
            BeforeLlmCallHookPayload(
                turn=_make_turn_context(kwargs["session_id"]),
                tools=[],
                token_budget=None,
            ),
            hook_context,
        )

    async def fake_after_llm_response(**kwargs):
        return AfterLlmResponseHookPayload(
            session_id=kwargs["hook_context"].session_id,
            task_type=kwargs["hook_context"].task_type,
            is_sub_session=False,
            enabled_groups=["CoreOperations"],
            message_text=None,
            tool_calls=[LlmToolCallRequest(call_id="tc_1", name="run_command", arguments_json='{"command":"pwd"}')],
            raw_llm_result=kwargs["llm_response"],
        )

    async def fake_run_stage(stage, hook_context, payload, enabled_groups=None):
        if stage == HookStage.BEFORE_TOOL_CALL:
            assert isinstance(payload, BeforeToolCallHookPayload)
            return HookStageOutput(
                status=HookStatus.OK,
                patched_payload=BeforeToolCallHookPayload(
                    request=payload.request,
                    session_id=payload.session_id,
                    call_index=payload.call_index,
                    enabled_groups=payload.enabled_groups,
                    force_finish=True,
                    force_finish_reason="tool_blocked",
                ),
            )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async def fake_execute(**kwargs):
        nonlocal executed
        executed = True
        return "ok"

    planner.resolve_llm_client = lambda task_type: stub_client
    planner.hook_stage_runner.run_stage = fake_run_stage
    planner.context_pipeline.run = fake_context_pipeline_run
    planner.tool_pipeline.run_after_llm_response = fake_after_llm_response
    planner.tool_runtime.execute = fake_execute
    planner_module.publish_runtime_event = fake_publish_runtime_event
    try:
        asyncio.run(
            planner.process_turn(
                TurnRequest(session_id="sess_force", user_segments=(), runtime=TurnRuntimePrefs(task_type="general"))
            )
        )
    finally:
        planner_module.publish_runtime_event = original_publish

    assert stub_client.called is True
    assert executed is False
    assert [event_type for event_type, _ in published] == [EventType.FORCE_FINISH_REQUESTED]


def test_after_tool_call_force_finish_skips_follow_up() -> None:
    planner = _build_planner()
    stub_client = StubClient(
        ChatResult(
            text="",
            tool_calls=[ToolCall(id="tc_1", name="run_command", arguments='{"command":"pwd"}')],
        )
    )
    published: list[tuple[EventType, dict[str, object]]] = []
    executed = False
    original_publish = planner_module.publish_runtime_event

    async def fake_publish_runtime_event(event_type, session_id, payload=None, source="runtime") -> bool:
        published.append((event_type, payload or {}))
        return True

    async def fake_context_pipeline_run(**kwargs):
        hook_context = _make_hook_context(kwargs["session_id"])
        return (
            BeforeLlmCallHookPayload(
                turn=_make_turn_context(kwargs["session_id"]),
                tools=[],
                token_budget=None,
            ),
            hook_context,
        )

    async def fake_after_llm_response(**kwargs):
        return AfterLlmResponseHookPayload(
            session_id=kwargs["hook_context"].session_id,
            task_type=kwargs["hook_context"].task_type,
            is_sub_session=False,
            enabled_groups=["CoreOperations"],
            message_text=None,
            tool_calls=[LlmToolCallRequest(call_id="tc_1", name="run_command", arguments_json='{"command":"pwd"}')],
            raw_llm_result=kwargs["llm_response"],
        )

    async def fake_run_stage(stage, hook_context, payload, enabled_groups=None):
        if stage == HookStage.AFTER_TOOL_CALL:
            assert isinstance(payload, AfterToolCallHookPayload)
            return HookStageOutput(
                status=HookStatus.OK,
                patched_payload=AfterToolCallHookPayload(
                    request=payload.request,
                    session_id=payload.session_id,
                    call_index=payload.call_index,
                    result=payload.result,
                    tool_status=payload.tool_status,
                    force_finish=True,
                    force_finish_reason="stop_after_tool",
                ),
            )
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async def fake_execute(**kwargs):
        nonlocal executed
        executed = True
        return "ok"

    planner.resolve_llm_client = lambda task_type: stub_client
    planner.hook_stage_runner.run_stage = fake_run_stage
    planner.context_pipeline.run = fake_context_pipeline_run
    planner.tool_pipeline.run_after_llm_response = fake_after_llm_response
    planner.tool_runtime.execute = fake_execute
    planner_module.publish_runtime_event = fake_publish_runtime_event
    try:
        asyncio.run(
            planner.process_turn(
                TurnRequest(session_id="sess_force", user_segments=(), runtime=TurnRuntimePrefs(task_type="general"))
            )
        )
    finally:
        planner_module.publish_runtime_event = original_publish

    assert stub_client.called is True
    assert executed is True
    assert [event_type for event_type, _ in published] == [EventType.FORCE_FINISH_REQUESTED]


def test_length_truncation_auto_repair_retries_and_executes_valid_tool() -> None:
    planner = _build_planner()
    stub_client = SequencedStubClient(
        [
            ChatResult(
                text="",
                tool_calls=[ToolCall(id="tc_1", name="run_command", arguments='{"command":"pwd"')],
                finish_reason="length",
            ),
            ChatResult(
                text="",
                tool_calls=[ToolCall(id="tc_2", name="run_command", arguments='{"command":"pwd"}')],
                finish_reason="tool_calls",
            ),
        ]
    )
    executed_args: list[str] = []

    async def fake_run_stage(stage, hook_context, payload, enabled_groups=None):
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async def fake_context_pipeline_run(**kwargs):
        hook_context = _make_hook_context(kwargs["session_id"])
        return (
            BeforeLlmCallHookPayload(
                turn=_make_turn_context(kwargs["session_id"]),
                tools=[],
                token_budget=None,
            ),
            hook_context,
        )

    async def fake_after_llm_response(**kwargs):
        return AfterLlmResponseHookPayload(
            session_id=kwargs["hook_context"].session_id,
            task_type=kwargs["hook_context"].task_type,
            is_sub_session=False,
            enabled_groups=["CoreOperations"],
            message_text=None,
            tool_calls=kwargs["tool_calls"],
            raw_llm_result=kwargs["llm_response"],
        )

    async def fake_execute(**kwargs):
        executed_args.append(kwargs["arguments_json"])
        return "ok"

    planner.resolve_llm_client = lambda task_type: stub_client
    planner.hook_stage_runner.run_stage = fake_run_stage
    planner.context_pipeline.run = fake_context_pipeline_run
    planner.tool_pipeline.run_after_llm_response = fake_after_llm_response
    planner.tool_runtime.execute = fake_execute

    asyncio.run(
        planner.process_turn(TurnRequest(session_id="sess_force", user_segments=(), runtime=TurnRuntimePrefs(task_type="general")))
    )

    assert stub_client.called == 2
    assert executed_args == ['{"command":"pwd"}']


def test_length_truncation_auto_repair_still_invalid_skips_tool_execution() -> None:
    planner = _build_planner()
    stub_client = SequencedStubClient(
        [
            ChatResult(
                text="",
                tool_calls=[ToolCall(id="tc_1", name="run_command", arguments='{"command":"pwd"')],
                finish_reason="length",
            ),
            ChatResult(
                text="",
                tool_calls=[ToolCall(id="tc_2", name="run_command", arguments='{"command":"pwd"')],
                finish_reason="length",
            ),
        ]
    )
    executed = False

    async def fake_run_stage(stage, hook_context, payload, enabled_groups=None):
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    async def fake_context_pipeline_run(**kwargs):
        hook_context = _make_hook_context(kwargs["session_id"])
        return (
            BeforeLlmCallHookPayload(
                turn=_make_turn_context(kwargs["session_id"]),
                tools=[],
                token_budget=None,
            ),
            hook_context,
        )

    async def fake_after_llm_response(**kwargs):
        return AfterLlmResponseHookPayload(
            session_id=kwargs["hook_context"].session_id,
            task_type=kwargs["hook_context"].task_type,
            is_sub_session=False,
            enabled_groups=["CoreOperations"],
            message_text=kwargs["llm_response"].text,
            tool_calls=kwargs["tool_calls"],
            raw_llm_result=kwargs["llm_response"],
        )

    async def fake_execute(**kwargs):
        nonlocal executed
        executed = True
        return "ok"

    planner.resolve_llm_client = lambda task_type: stub_client
    planner.hook_stage_runner.run_stage = fake_run_stage
    planner.context_pipeline.run = fake_context_pipeline_run
    planner.tool_pipeline.run_after_llm_response = fake_after_llm_response
    planner.tool_runtime.execute = fake_execute
    asyncio.run(
        planner.process_turn(
            TurnRequest(session_id="sess_force", user_segments=(), runtime=TurnRuntimePrefs(task_type="general"))
        )
    )

    assert stub_client.called == 2
    assert executed is False

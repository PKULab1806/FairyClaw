# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
from pathlib import Path

from fairyclaw.capabilities.compression_hooks.scripts.context_compression import execute_hook as execute_before_llm_hook
from fairyclaw.capabilities.compression_hooks.scripts._unloaded_segments_state import load_unloaded_segments_state
from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, TextBody, ToolCallRound, UserTurn
from fairyclaw.core.agent.hooks.protocol import (
    HookExecutionContext,
    HookStage,
    HookStageInput,
    HookStageOutput,
)
from fairyclaw.core.agent.hooks.runtime import HookRuntime
from fairyclaw.core.agent.hooks.protocol import (
    AfterToolCallHookPayload,
    BeforeLlmCallHookPayload,
    BeforeToolCallHookPayload,
    HookStatus,
    LlmChatMessage,
    LlmToolCallRequest,
    LlmTurnContext,
    to_openai_messages,
)
from fairyclaw.core.capabilities.models import HookDefinition
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.events.bus import EventType, RuntimeEvent
from fairyclaw.core.events.payloads import (
    FileUploadReceivedEventPayload,
    ForceFinishRequestedEventPayload,
    payload_from_runtime_event,
)


def test_before_and_after_tool_payload_models() -> None:
    request = LlmToolCallRequest(call_id="tc_1", name="run_command", arguments_json='{"command":"pwd"}')
    before_payload = BeforeToolCallHookPayload(
        request=request,
        session_id="sess_1",
        call_index=0,
        enabled_groups=["CoreOperations"],
        force_finish=True,
        force_finish_reason="blocked_by_hook",
    )
    assert before_payload.request.name == "run_command"
    assert before_payload.force_finish is True

    after_payload = AfterToolCallHookPayload(
        request=request,
        session_id="sess_1",
        call_index=0,
        result="Stdout:/tmp",
        tool_status="ok",
        force_finish=True,
        force_finish_reason="batch_done",
    )
    assert after_payload.result == "Stdout:/tmp"
    assert after_payload.force_finish_reason == "batch_done"


def test_before_llm_call_payload_with_typed_messages() -> None:
    turn = LlmTurnContext(
        llm_messages=[LlmChatMessage(role="system", content="system"), LlmChatMessage(role="user", content="hello")],
        history_items=[],
        user_turn=None,
        session_id="sess_1",
        task_type="general",
        is_sub_session=False,
    )
    payload = BeforeLlmCallHookPayload(turn=turn, tools=[], token_budget=1024)
    openai_messages = to_openai_messages(payload.turn.llm_messages)
    assert openai_messages[0] == {"role": "system", "content": "system"}
    assert openai_messages[1] == {"role": "user", "content": "hello"}


def test_file_upload_event_payload_contains_session_fields() -> None:
    event = RuntimeEvent(
        type=EventType.FILE_UPLOAD_RECEIVED,
        session_id="sess_42",
        payload={"file_id": "file_1", "filename": "a.txt", "mime_type": "text/plain"},
        source="files_router",
    )
    payload = payload_from_runtime_event(event)
    assert isinstance(payload, FileUploadReceivedEventPayload)
    assert payload.session_id == "sess_42"
    assert payload.file_id == "file_1"


def test_force_finish_event_payload_contains_stage_and_reason() -> None:
    event = RuntimeEvent(
        type=EventType.FORCE_FINISH_REQUESTED,
        session_id="sess_force",
        payload={
            "reason": "hook_requested_stop",
            "stage": "before_llm_call",
            "turn_id": "turn_1",
            "task_type": "general",
            "enabled_groups": ["CoreOperations"],
            "is_sub_session": False,
            "details": {"source_hook": "context_compression"},
        },
        source="planner_force_finish",
    )
    payload = payload_from_runtime_event(event)
    assert isinstance(payload, ForceFinishRequestedEventPayload)
    assert payload.session_id == "sess_force"
    assert payload.stage == "before_llm_call"
    assert payload.reason == "hook_requested_stop"
    assert payload.enabled_groups == ["CoreOperations"]


async def _rewrite_last_message(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    payload = hook_input.payload
    updated_messages = list(payload.turn.llm_messages)
    updated_messages[-1] = LlmChatMessage(role="user", content="updated")
    return HookStageOutput(
        status=HookStatus.OK,
        patched_payload=BeforeLlmCallHookPayload(
            turn=LlmTurnContext(
                llm_messages=updated_messages,
                history_items=payload.turn.history_items,
                user_turn=payload.turn.user_turn,
                session_id=payload.turn.session_id,
                task_type=payload.turn.task_type,
                is_sub_session=payload.turn.is_sub_session,
            ),
            tools=payload.tools,
            token_budget=payload.token_budget,
        ),
    )


def test_hook_runtime_chains_same_typed_payload() -> None:
    runtime = HookRuntime()
    payload = BeforeLlmCallHookPayload(
        turn=LlmTurnContext(
            llm_messages=[LlmChatMessage(role="system", content="system"), LlmChatMessage(role="user", content="hello")],
            history_items=[SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="hello"))],
            user_turn=None,
            session_id="sess_1",
            task_type="general",
            is_sub_session=False,
        ),
        tools=[],
        token_budget=1024,
    )
    hook_input = HookStageInput(
        stage=HookStage.BEFORE_LLM_CALL,
        context=HookExecutionContext(
            session_id="sess_1",
            turn_id="turn_1",
            task_type="general",
            is_sub_session=False,
        ),
        payload=payload,
    )
    hooks = [
        (
            HookDefinition(name="rewrite_message", stage="before_llm_call", script="noop.py"),
            _rewrite_last_message,
        ),
    ]
    output = asyncio.run(runtime.run_stage(hook_input, hooks))
    assert output.patched_payload is not None
    assert output.patched_payload.turn.llm_messages[-1].content == "updated"


def test_before_llm_hook_demo_reads_typed_history_ir() -> None:
    payload = BeforeLlmCallHookPayload(
        turn=LlmTurnContext(
            llm_messages=[LlmChatMessage(role="user", content="hello")],
            history_items=[
                SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="question")),
                ToolCallRound(
                    tool_name="run_command",
                    call_id="tc_1",
                    arguments_json='{"command":"pwd"}',
                    tool_result="ok",
                ),
            ],
            user_turn=None,
            session_id="sess_1",
            task_type="general",
            is_sub_session=False,
        ),
        tools=[],
        token_budget=1024,
    )
    result = asyncio.run(
        execute_before_llm_hook(
            HookStageInput(
                stage=HookStage.BEFORE_LLM_CALL,
                context=HookExecutionContext(
                    session_id="sess_1",
                    turn_id="turn_1",
                    task_type="general",
                    is_sub_session=False,
                ),
                payload=payload,
            )
        )
    )
    assert result.patched_payload is payload


def test_before_llm_hook_preserves_tool_round_suffix_when_rebuilding_user_turn() -> None:
    payload = BeforeLlmCallHookPayload(
        turn=LlmTurnContext(
            llm_messages=[
                LlmChatMessage(role="system", content="system"),
                LlmChatMessage(role="assistant", content="Search first.", tool_calls=[LlmToolCallRequest("tc_1", "search_web", '{"q":"x"}')]),
                LlmChatMessage(role="tool", tool_call_id="tc_1", name="search_web", content="result"),
                LlmChatMessage(role="user", content="Continue"),
            ],
            history_items=[
                SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="Search first.")),
                ToolCallRound(
                    tool_name="search_web",
                    call_id="tc_1",
                    arguments_json='{"q":"x"}',
                    tool_result="result",
                ),
            ],
            user_turn=UserTurn(message=SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="Continue"))),
            session_id="sess_1",
            task_type="general",
            is_sub_session=False,
        ),
        tools=[],
        token_budget=1,
    )
    result = asyncio.run(
        execute_before_llm_hook(
            HookStageInput(
                stage=HookStage.BEFORE_LLM_CALL,
                context=HookExecutionContext(
                    session_id="sess_1",
                    turn_id="turn_1",
                    task_type="general",
                    is_sub_session=False,
                ),
                payload=payload,
            )
        )
    )
    assert result.patched_payload is not None
    # Latest assistant + tool rounds are kept as one protected suffix so provider messages stay consistent
    # (see context_compression: do not strip ToolCallRound globally).
    assert any(isinstance(item, ToolCallRound) for item in result.patched_payload.turn.history_items)
    rebuilt_messages = result.patched_payload.turn.llm_messages
    assert rebuilt_messages[-1].role == "user"


def test_before_llm_hook_preserves_injected_system_messages_during_rebuild() -> None:
    payload = BeforeLlmCallHookPayload(
        turn=LlmTurnContext(
            llm_messages=[
                LlmChatMessage(role="system", content="system"),
                LlmChatMessage(role="system", content="[RecalledMemory]\n1. fact\n[/RecalledMemory]"),
                LlmChatMessage(role="assistant", content="older assistant"),
                LlmChatMessage(role="user", content="Continue"),
            ],
            history_items=[
                SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="older assistant")),
            ],
            user_turn=UserTurn(message=SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="Continue"))),
            session_id="sess_1",
            task_type="general",
            is_sub_session=False,
        ),
        tools=[],
        token_budget=1,
    )
    result = asyncio.run(
        execute_before_llm_hook(
            HookStageInput(
                stage=HookStage.BEFORE_LLM_CALL,
                context=HookExecutionContext(
                    session_id="sess_1",
                    turn_id="turn_1",
                    task_type="general",
                    is_sub_session=False,
                    token_budget=1,
                ),
                payload=payload,
            )
        )
    )
    assert result.patched_payload is not None
    rebuilt_messages = result.patched_payload.turn.llm_messages
    assert rebuilt_messages[1].role == "system"
    assert "RecalledMemory" in str(rebuilt_messages[1].content)


def _tiny_png_data_url() -> str:
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO7Z7xkAAAAASUVORK5CYII="
    )
    return f"data:image/png;base64,{png_b64}"


def test_before_llm_hook_unloads_old_image_segments_instead_of_dropping(tmp_path: Path, monkeypatch) -> None:
    from fairyclaw.capabilities.compression_hooks.scripts import _unloaded_segments_state as state_mod
    from fairyclaw.capabilities.compression_hooks.scripts import context_compression as compression_mod

    monkeypatch.setattr(state_mod, "resolve_memory_root", lambda mkdir=True: tmp_path)
    monkeypatch.setattr(compression_mod, "_count_prompt_tokens", lambda counter, messages, payload: 100)
    rebuilt_counts = iter([100, 100, 100, 0, 0])
    monkeypatch.setattr(
        compression_mod,
        "_count_rebuilt_prompt",
        lambda counter, payload, history_items, system_prompt, extra_system_messages: next(rebuilt_counts),
    )
    payload = BeforeLlmCallHookPayload(
        turn=LlmTurnContext(
            llm_messages=[LlmChatMessage(role="system", content="system"), LlmChatMessage(role="user", content="look")],
            history_items=[
                SessionMessageBlock.from_segments(
                    SessionMessageRole.USER,
                    (ContentSegment.image_url_segment(_tiny_png_data_url()),),
                )
                or SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="fallback")),
                SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="recent assistant")),
            ],
            user_turn=UserTurn(message=SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="look"))),
            session_id="sess_img_unload",
            task_type="image",
            is_sub_session=False,
        ),
        tools=[],
        token_budget=20,
    )
    result = asyncio.run(
        execute_before_llm_hook(
            HookStageInput(
                stage=HookStage.BEFORE_LLM_CALL,
                context=HookExecutionContext(
                    session_id="sess_img_unload",
                    turn_id="turn_img_unload",
                    task_type="image",
                    is_sub_session=False,
                    token_budget=20,
                ),
                payload=payload,
            )
        )
    )
    assert result.patched_payload is not None
    rebuilt_history = result.patched_payload.turn.history_items
    assert rebuilt_history
    assert isinstance(rebuilt_history[0], SessionMessageBlock)
    placeholder_text = rebuilt_history[0].as_plain_text()
    assert "image context unloaded" in placeholder_text
    state = load_unloaded_segments_state(session_id="sess_img_unload", memory_root=str(tmp_path))
    assert len(state["records"]) == 1

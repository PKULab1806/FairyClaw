# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.core.agent.context.history_ir import (
    SegmentsBody,
    SessionMessageBlock,
    SessionMessageRole,
    TextBody,
    ToolCallRound,
    UserTurn,
)
from fairyclaw.core.domain import ContentSegment


def test_map_session_entry_text_to_session_message_block() -> None:
    entry = SessionMessageBlock.from_segments(
        SessionMessageRole.ASSISTANT,
        (ContentSegment.text_segment("hello"),),
    )
    assert entry is not None
    assert entry.role == SessionMessageRole.ASSISTANT
    assert isinstance(entry.body, TextBody)
    assert entry.body.text == "hello"


def test_map_operation_entry_to_tool_call_round() -> None:
    entry = ToolCallRound.from_persisted(
        event_id="evt_123",
        tool_name="run_command",
        tool_args={"tool_call_id": "tc_1", "arguments_json": "{\"command\":\"pwd\"}"},
        tool_result="ok",
    )
    assert isinstance(entry, ToolCallRound)
    assert entry.tool_name == "run_command"
    assert entry.call_id == "tc_1"
    assert entry.arguments_json == "{\"command\":\"pwd\"}"
    assert entry.tool_result == "ok"


def test_map_user_message_to_user_turn_with_segments_body() -> None:
    user_turn = UserTurn.from_segments(
        (
            ContentSegment.text_segment("hello"),
            ContentSegment.image_url_segment("https://example.com/a.png"),
        )
    )
    assert isinstance(user_turn, UserTurn)
    assert user_turn.message.role == SessionMessageRole.USER
    assert isinstance(user_turn.message.body, SegmentsBody)
    assert user_turn.message.as_plain_text() == "hello"

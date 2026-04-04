# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, TextBody
from fairyclaw.core.agent.context.llm_message_assembler import LlmMessageAssembler
from fairyclaw.core.agent.context.turn_context_builder import TurnContextBuilder
from fairyclaw.core.domain import ContentSegment


def test_build_extracts_current_user_turn_from_history_when_request_has_no_segments() -> None:
    builder = TurnContextBuilder(LlmMessageAssembler())
    messages, history_items, user_turn = builder.build(
        history_items=[
            SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="previous")),
            SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="current user")),
        ],
        user_segments=(),
        session_id="sess_1",
        task_type="general",
    )
    assert user_turn is not None
    assert user_turn.message.as_plain_text() == "current user"
    assert len(history_items) == 1
    assert history_items[0].as_plain_text() == "previous"
    assert messages[-1].role == "user"
    assert messages[-1].content == "current user"


def test_build_deduplicates_explicit_user_turn_against_latest_history_user() -> None:
    builder = TurnContextBuilder(LlmMessageAssembler())
    messages, history_items, user_turn = builder.build(
        history_items=[
            SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="previous")),
            SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="current user")),
        ],
        user_segments=(ContentSegment.text_segment("current user"),),
        session_id="sess_1",
        task_type="general",
    )
    assert user_turn is not None
    assert user_turn.message.as_plain_text() == "current user"
    assert len(history_items) == 1
    assert history_items[0].as_plain_text() == "previous"
    assert [message.role for message in messages[-2:]] == ["assistant", "user"]

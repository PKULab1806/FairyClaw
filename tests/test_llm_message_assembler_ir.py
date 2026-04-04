# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.core.agent.context.history_ir import (
    SessionMessageBlock,
    SessionMessageRole,
    TextBody,
    ToolCallRound,
    UserTurn,
)
from fairyclaw.core.agent.context.llm_message_assembler import LlmMessageAssembler
from fairyclaw.core.agent.hooks.protocol import to_openai_messages
from fairyclaw.core.agent.types import SystemPromptPart


def test_assemble_from_typed_ir() -> None:
    assembler = LlmMessageAssembler()
    history_entries = [
        SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="question")),
        ToolCallRound(
            tool_name="run_command",
            call_id="tc_1_abc",
            arguments_json='{"command":"pwd"}',
            tool_result="Stdout:/tmp",
        ),
    ]
    user_turn = UserTurn(message=SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="next")))
    messages = assembler.assemble(
        system_prompt=SystemPromptPart(text="system"),
        history_entries=history_entries,
        user_entry=user_turn,
    )
    openai_messages = to_openai_messages(messages)
    assert openai_messages[0] == {"role": "system", "content": "system"}
    assert openai_messages[1] == {"role": "user", "content": "question"}
    assert openai_messages[2]["role"] == "assistant"
    assert openai_messages[2]["tool_calls"][0]["function"]["name"] == "run_command"
    assert openai_messages[3]["role"] == "tool"
    assert openai_messages[3]["tool_call_id"] == "tc_1_abc"
    assert openai_messages[-1] == {"role": "user", "content": "next"}


def test_assemble_groups_consecutive_tool_rounds_into_one_assistant_message() -> None:
    assembler = LlmMessageAssembler()
    history_entries = [
        ToolCallRound(
            tool_name="run_command",
            call_id="tc_1",
            arguments_json='{"command":"pwd"}',
            tool_result="Stdout:/tmp",
        ),
        ToolCallRound(
            tool_name="read_file",
            call_id="tc_2",
            arguments_json='{"path":"README.md"}',
            tool_result="file content",
        ),
    ]
    messages = assembler.assemble(
        system_prompt=SystemPromptPart(text="system"),
        history_entries=history_entries,
        user_entry=None,
    )
    openai_messages = to_openai_messages(messages)
    assert openai_messages[1]["role"] == "assistant"
    assert openai_messages[1]["content"] == ""
    assert len(openai_messages[1]["tool_calls"]) == 2
    assert openai_messages[2]["role"] == "tool"
    assert openai_messages[3]["role"] == "tool"


def test_assemble_preserves_assistant_content_before_tool_batch() -> None:
    assembler = LlmMessageAssembler()
    history_entries = [
        SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="先并行查三个框架。")),
        ToolCallRound(
            tool_name="delegate_task",
            call_id="tc_1",
            arguments_json='{"instruction":"python"}',
            tool_result="subtask 1",
        ),
        ToolCallRound(
            tool_name="delegate_task",
            call_id="tc_2",
            arguments_json='{"instruction":"go"}',
            tool_result="subtask 2",
        ),
    ]
    messages = assembler.assemble(
        system_prompt=SystemPromptPart(text="system"),
        history_entries=history_entries,
        user_entry=None,
    )
    openai_messages = to_openai_messages(messages)
    assert openai_messages[1]["role"] == "assistant"
    assert openai_messages[1]["content"] == "先并行查三个框架。"
    assert len(openai_messages[1]["tool_calls"]) == 2
    assert openai_messages[2]["tool_call_id"] == "tc_1"
    assert openai_messages[3]["tool_call_id"] == "tc_2"


def test_assemble_does_not_merge_tool_calls_across_different_turns_without_content() -> None:
    assembler = LlmMessageAssembler()
    history_entries = [
        # Turn A: one tool call, no assistant text content persisted.
        ToolCallRound(
            tool_name="run_command",
            call_id="tc_1_turnA",
            arguments_json='{"command":"pwd"}',
            tool_result="A",
        ),
        # Turn B: one tool call, also no assistant text persisted.
        # Ordinal resets to tc_1_*, should start a new assistant tool-call message.
        ToolCallRound(
            tool_name="read_file",
            call_id="tc_1_turnB",
            arguments_json='{"path":"README.md"}',
            tool_result="B",
        ),
    ]
    messages = assembler.assemble(
        system_prompt=SystemPromptPart(text="system"),
        history_entries=history_entries,
        user_entry=None,
    )
    openai_messages = to_openai_messages(messages)
    assistant_tool_msgs = [message for message in openai_messages if message["role"] == "assistant" and "tool_calls" in message]
    assert len(assistant_tool_msgs) == 2
    assert len(assistant_tool_msgs[0]["tool_calls"]) == 1
    assert len(assistant_tool_msgs[1]["tool_calls"]) == 1
    assert assistant_tool_msgs[0]["tool_calls"][0]["id"] == "tc_1_turnA"
    assert assistant_tool_msgs[1]["tool_calls"][0]["id"] == "tc_1_turnB"

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, TextBody, ToolCallRound
from fairyclaw.core.agent.hooks.protocol import LlmChatMessage, LlmToolCallRequest
from fairyclaw.infrastructure.embedding.service import HashingEmbedding
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter


def test_token_counter_counts_messages_and_history_items() -> None:
    counter = TokenCounter(model="gpt-4")
    messages = [
        LlmChatMessage(role="system", content="system prompt"),
        LlmChatMessage(
            role="assistant",
            content="working",
            tool_calls=[LlmToolCallRequest(call_id="tc_1", name="run_command", arguments_json='{"command":"pwd"}')],
        ),
    ]
    history = [
        SessionMessageBlock(role=SessionMessageRole.USER, body=TextBody(text="hello")),
        ToolCallRound(
            tool_name="run_command",
            call_id="tc_1",
            arguments_json='{"command":"pwd"}',
            tool_result="ok",
        ),
    ]
    assert counter.count_messages(messages) > 0
    assert counter.count_history(history) > 0


def test_hashing_embedding_is_deterministic_and_normalized() -> None:
    service = HashingEmbedding(model_name="hashing-384", dimensions=384)
    vectors = asyncio.run(service.embed(["alpha beta", "alpha beta"]))
    assert len(vectors) == 2
    assert vectors[0] == vectors[1]
    norm = sum(value * value for value in vectors[0]) ** 0.5
    assert round(norm, 6) == 1.0

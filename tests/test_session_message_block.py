# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.core.agent.context.history_ir import SegmentsBody, SessionMessageBlock, SessionMessageRole, TextBody
from fairyclaw.core.domain import ContentSegment


def test_text_body_plain_text_and_openai_content() -> None:
    block = SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="hello"))
    assert block.as_plain_text() == "hello"
    assert block.as_openai_content() == "hello"


def test_segments_body_plain_text_and_openai_content() -> None:
    body = SegmentsBody(
        segments=(
            ContentSegment.text_segment("hello"),
            ContentSegment.image_url_segment("https://example.com/a.png"),
            ContentSegment.text_segment("world"),
        )
    )
    block = SessionMessageBlock(role=SessionMessageRole.USER, body=body)
    assert block.as_plain_text() == "hello\nworld"
    openai_content = block.as_openai_content()
    assert isinstance(openai_content, list)
    assert openai_content[0]["type"] == "text"
    assert openai_content[1]["type"] == "image_url"

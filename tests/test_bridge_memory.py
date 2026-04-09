# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for UserGateway outbound emits (replaces BridgeOutputMemory)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, ToolCallRound
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.gateway_protocol.models import OUTBOUND_KIND_EVENT, OUTBOUND_KIND_TEXT
from fairyclaw.core.agent.session.memory import PersistentMemory


def test_user_gateway_emit_assistant_and_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        from fairyclaw.bridge import user_gateway as ug_mod

        pushed: list = []

        async def capture(msg: object) -> None:
            pushed.append(msg)

        gw = ug_mod.UserGateway(bus=MagicMock())
        monkeypatch.setattr(gw, "push_outbound", capture)

        assistant_message = SessionMessageBlock.from_segments(
            SessionMessageRole.ASSISTANT,
            (ContentSegment.text_segment("hello"),),
        )
        assert assistant_message is not None

        base = MagicMock(spec=PersistentMemory)
        base.add_session_event = AsyncMock()
        await base.add_session_event("sess_main", assistant_message)
        await gw.emit_assistant_text("sess_main", "hello")

        tr = ToolCallRound(
            tool_name="send_file",
            call_id="call_1",
            arguments_json='{"file_path":"/srv/project/out.txt"}',
            tool_result="File sent to user.",
            success=True,
        )
        await gw.emit_tool_result("sess_main", tr)

        assert len(pushed) == 2
        assert pushed[0].kind == OUTBOUND_KIND_TEXT
        assert pushed[0].content.get("text") == "hello"
        assert pushed[1].kind == OUTBOUND_KIND_EVENT
        assert pushed[1].content.get("event_type") == "tool_result"

    asyncio.run(scenario())


def test_user_gateway_skips_sub_session_assistant_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        from fairyclaw.bridge import user_gateway as ug_mod

        pushed: list = []

        async def capture(msg: object) -> None:
            pushed.append(msg)

        gw = ug_mod.UserGateway(bus=MagicMock())
        monkeypatch.setattr(gw, "push_outbound", capture)

        assistant_message = SessionMessageBlock.from_segments(
            SessionMessageRole.ASSISTANT,
            (ContentSegment.text_segment("sub reply"),),
        )
        assert assistant_message is not None
        await gw.emit_assistant_text("sess_sub_1", "sub reply")

        assert pushed == []

    asyncio.run(scenario())

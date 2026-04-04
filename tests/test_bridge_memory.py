# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from fairyclaw.bridge.bridge_memory import BridgeOutputMemory
from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, ToolCallRound
from fairyclaw.core.agent.interfaces.memory_provider import MemoryProvider
from fairyclaw.core.domain import ContentSegment


class FakeMemory(MemoryProvider):
    def __init__(self) -> None:
        self.session_events: list[tuple[str, str]] = []
        self.operation_events: list[ToolCallRound] = []

    async def get_history(self, session_id: str, limit: int = 50) -> list:
        return []

    async def add_session_event(self, session_id: str, message) -> None:
        self.session_events.append((session_id, message.as_plain_text()))

    async def add_operation_event(self, session_id: str, tool_round: ToolCallRound) -> None:
        self.operation_events.append(tool_round)


def test_bridge_output_memory_pushes_main_session_text_and_redacts_send_file() -> None:
    async def scenario() -> None:
        base = FakeMemory()
        pushed: list[tuple[str, dict[str, str]]] = []

        async def push_outbound(message) -> None:
            pushed.append((message.session_id, dict(message.content)))

        memory = BridgeOutputMemory(base=base, push_outbound=push_outbound)
        assistant_message = SessionMessageBlock.from_segments(
            SessionMessageRole.ASSISTANT,
            (ContentSegment.text_segment("hello"),),
        )
        assert assistant_message is not None

        await memory.add_session_event("sess_main", assistant_message)
        await memory.add_operation_event(
            "sess_main",
            ToolCallRound(
                tool_name="send_file",
                call_id="call_1",
                arguments_json='{"file_path":"/tmp/demo.txt"}',
                tool_result='{"status":"sent","file_id":"file_1"}',
            ),
        )

        assert base.session_events == [("sess_main", "hello")]
        assert pushed == [("sess_main", {"text": "hello"})]
        persisted = base.operation_events[0]
        assert persisted.arguments_json == '{"file_path":"(omitted)"}'
        assert persisted.tool_result == "File sent to user."

    asyncio.run(scenario())


def test_bridge_output_memory_skips_sub_session_text_push() -> None:
    async def scenario() -> None:
        base = FakeMemory()
        pushed: list[tuple[str, dict[str, str]]] = []

        async def push_outbound(message) -> None:
            pushed.append((message.session_id, dict(message.content)))

        memory = BridgeOutputMemory(base=base, push_outbound=push_outbound)
        assistant_message = SessionMessageBlock.from_segments(
            SessionMessageRole.ASSISTANT,
            (ContentSegment.text_segment("sub reply"),),
        )
        assert assistant_message is not None

        await memory.add_session_event("sess_sub_1", assistant_message)

        assert base.session_events == [("sess_sub_1", "sub reply")]
        assert pushed == []

    asyncio.run(scenario())

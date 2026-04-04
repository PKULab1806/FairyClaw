# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from types import SimpleNamespace

import asyncio

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, TextBody, ToolCallRound
from fairyclaw.core.agent.session.memory import PersistentMemory


class FakeRepo:
    def __init__(self) -> None:
        self.session_events: list[dict[str, object]] = []
        self.operation_events: list[dict[str, object]] = []
        self.db = object()

    async def history(self, session_id: str, limit: int = 50) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id="evt_session",
                type="session_event",
                role="assistant",
                content=[{"type": "text", "text": "hello"}],
                tool_name=None,
                tool_args=None,
                tool_result=None,
            ),
            SimpleNamespace(
                id="evt_op",
                type="operation_event",
                role=None,
                content=None,
                tool_name="run_command",
                tool_args={"tool_call_id": "tc_1", "arguments_json": '{"command":"pwd"}'},
                tool_result="ok",
            ),
        ]

    async def add_session_event(self, session_id: str, role: str, content: list[dict[str, object]]) -> None:
        self.session_events.append({"session_id": session_id, "role": role, "content": content})

    async def add_operation_event(
        self,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, str],
        tool_result: str,
    ) -> None:
        self.operation_events.append(
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": tool_result,
            }
        )


class FakeCompactionRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def latest_snapshot(self, session_id: str):
        if not self.created:
            return None
        return SimpleNamespace(**self.created[-1])

    async def create_snapshot(
        self,
        session_id: str,
        strategy: str,
        summary_text: str,
        key_facts: dict[str, object] | None = None,
        from_event_id: str | None = None,
        to_event_id: str | None = None,
        created_by: str = "auto",
    ):
        model = {
            "session_id": session_id,
            "strategy": strategy,
            "summary_text": summary_text,
            "key_facts": key_facts or {},
            "from_event_id": from_event_id,
            "to_event_id": to_event_id,
            "created_by": created_by,
        }
        self.created.append(model)
        return SimpleNamespace(**model)


def test_get_history_returns_typed_ir() -> None:
    memory = PersistentMemory(FakeRepo())
    history = asyncio.run(memory.get_history("sess_1"))
    assert isinstance(history[0], SessionMessageBlock)
    assert history[0].as_plain_text() == "hello"
    assert isinstance(history[1], ToolCallRound)
    assert history[1].call_id == "tc_1"


def test_add_session_and_operation_events_use_typed_inputs() -> None:
    repo = FakeRepo()
    memory = PersistentMemory(repo)
    asyncio.run(
        memory.add_session_event(
            "sess_1",
            SessionMessageBlock(role=SessionMessageRole.ASSISTANT, body=TextBody(text="done")),
        )
    )
    asyncio.run(
        memory.add_operation_event(
            "sess_1",
            ToolCallRound(
                tool_name="run_command",
                call_id="tc_1",
                arguments_json='{"command":"pwd"}',
                tool_result="ok",
            ),
        ),
    )
    assert repo.session_events[0]["content"] == [{"type": "text", "text": "done"}]
    assert repo.operation_events[0]["tool_args"] == {"tool_call_id": "tc_1", "arguments_json": '{"command":"pwd"}'}


def test_compaction_snapshot_roundtrip_uses_structured_contract() -> None:
    memory = PersistentMemory(FakeRepo())
    memory._compaction_repo = FakeCompactionRepo()  # type: ignore[assignment]
    snapshot = asyncio.run(
        memory.create_compaction_snapshot(
            "sess_1",
            strategy="anchored_summary",
            summary_text="summary",
            key_facts={"recent_tools": ["run_command"]},
            to_event_id="evt_2",
        )
    )
    assert snapshot is not None
    assert snapshot.summary_text == "summary"
    latest = asyncio.run(memory.get_latest_compaction("sess_1"))
    assert latest is not None
    assert latest.to_event_id == "evt_2"

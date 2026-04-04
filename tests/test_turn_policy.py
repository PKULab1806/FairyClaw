# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from fairyclaw.core.agent.planning.turn_policy import MainSessionTurnPolicy, SubSessionTurnPolicy
from fairyclaw.core.agent.types import SessionKind, TurnRequest, TurnRuntimePrefs


class StubSubtasks:
    def __init__(self, terminal: bool = False) -> None:
        self.terminal = terminal
        self.barrier_published = False

    def is_sub_session_terminal(self, session_id: str) -> bool:
        return self.terminal

    async def publish_subtask_barrier_if_ready(self, session_id: str) -> None:
        self.barrier_published = True

    async def mark_subtask_if_non_terminal(self, session_id: str, status: str, summary: str) -> None:
        self.terminal = True

    def lookup_subtask_status(self, session_id: str) -> str | None:
        return "failed"

    async def notify_main_session_subtask_failure(self, session_id: str, summary: str, status: str = "failed") -> None:
        return None


class StubPlanner:
    def __init__(self, terminal: bool = False) -> None:
        self.subtasks = StubSubtasks(terminal=terminal)
        self.follow_ups: list[tuple[str, str, list[str]]] = []
        self.logger = type("Logger", (), {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None})()

    def _should_publish_follow_up(self, called_tools: list[str]) -> bool:
        return bool(called_tools)

    async def _publish_follow_up_event(self, session_id: str, task_type: str, enabled_groups: list[str]) -> None:
        self.follow_ups.append((session_id, task_type, enabled_groups))

    async def _handle_text_fallback(self, session_id: str, message_text: str, memory, callback) -> None:
        return None


def test_main_policy_publishes_follow_up_after_tools() -> None:
    planner = StubPlanner()
    policy = MainSessionTurnPolicy()
    request = TurnRequest(session_id="sess_main", user_segments=(), runtime=TurnRuntimePrefs(task_type="general"))

    asyncio_run(policy.handle_tool_follow_up(planner, request, "general", ["core"], ["run_command"]))

    assert planner.follow_ups == [("sess_main", "general", ["core"])]


def test_sub_policy_skips_terminal_session() -> None:
    planner = StubPlanner(terminal=True)
    policy = SubSessionTurnPolicy()
    request = TurnRequest(
        session_id="sess_main_sub_1",
        user_segments=(),
        runtime=TurnRuntimePrefs(task_type="general"),
        session_kind=SessionKind.SUB,
    )

    should_skip = asyncio_run(policy.should_skip(planner, request))

    assert should_skip is True


def asyncio_run(awaitable):
    return asyncio.run(awaitable)

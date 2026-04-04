# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Bridge-aware memory wrapper for outbound delivery side effects."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole, ToolCallRound
from fairyclaw.core.agent.interfaces.memory_provider import CompactionSnapshot, MemoryProvider
from fairyclaw.core.agent.session.session_role import resolve_session_role_policy
from fairyclaw.core.gateway_protocol.models import GatewayOutboundMessage

OutboundPusher = Callable[[GatewayOutboundMessage], Awaitable[None]]
_SEND_FILE_ARGS_PLACEHOLDER = '{"file_path":"(omitted)"}'
_SEND_FILE_RESULT_PLACEHOLDER = "File sent to user."


class BridgeOutputMemory(MemoryProvider):
    """Wrap memory writes and mirror assistant text to the gateway bridge."""

    def __init__(self, base: MemoryProvider, push_outbound: OutboundPusher | None) -> None:
        self._base = base
        self._push_outbound = push_outbound

    async def get_history(self, session_id: str, limit: int = 50) -> list:
        return await self._base.get_history(session_id=session_id, limit=limit)

    async def add_session_event(self, session_id: str, message: SessionMessageBlock) -> None:
        await self._base.add_session_event(session_id=session_id, message=message)
        if message.role is not SessionMessageRole.ASSISTANT:
            return
        if not resolve_session_role_policy(session_id).can_callback_user:
            return
        text = message.as_plain_text().strip()
        if not text or self._push_outbound is None:
            return
        await self._push_outbound(GatewayOutboundMessage.text(session_id=session_id, text=text))

    async def add_operation_event(self, session_id: str, tool_round: ToolCallRound) -> None:
        if tool_round.tool_name == "send_file":
            tool_round = ToolCallRound(
                tool_name="send_file",
                call_id=tool_round.call_id,
                arguments_json=_SEND_FILE_ARGS_PLACEHOLDER,
                tool_result=_SEND_FILE_RESULT_PLACEHOLDER,
            )
        await self._base.add_operation_event(session_id=session_id, tool_round=tool_round)

    async def get_latest_compaction(self, session_id: str) -> CompactionSnapshot | None:
        return await self._base.get_latest_compaction(session_id=session_id)

    async def create_compaction_snapshot(
        self,
        session_id: str,
        strategy: str,
        summary_text: str,
        key_facts: dict[str, object] | None = None,
        from_event_id: str | None = None,
        to_event_id: str | None = None,
        created_by: str = "auto",
    ) -> CompactionSnapshot | None:
        return await self._base.create_compaction_snapshot(
            session_id=session_id,
            strategy=strategy,
            summary_text=summary_text,
            key_facts=key_facts,
            from_event_id=from_event_id,
            to_event_id=to_event_id,
            created_by=created_by,
        )

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Business-side inbound gateway service."""

from __future__ import annotations

from typing import Any

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.core.events.bus import EventType, RuntimeEvent, SessionEventBus
from fairyclaw.core.domain import ContentSegment, SegmentType
from fairyclaw.core.gateway_protocol.models import GatewayInboundMessage
from fairyclaw.core.runtime.session_runtime_store import get_session_runtime_store
from fairyclaw.infrastructure.database.repository import EventRepository, FileRepository, SessionRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.infrastructure.files.file_kind import describe_user_upload_for_llm


async def _enrich_inbound_file_segments(
    session_id: str,
    segments: tuple[ContentSegment, ...],
    *,
    file_repo: FileRepository,
) -> tuple[ContentSegment, ...]:
    """Set file_kind_description on FILE segments using stored bytes (magic sniff). Inbound-only."""
    if not segments:
        return segments
    if not any(
        seg.type == SegmentType.FILE
        and bool(seg.file_id)
        and not (seg.file_kind_description and str(seg.file_kind_description).strip())
        for seg in segments
    ):
        return segments
    out: list[ContentSegment] = []
    for seg in segments:
        if (
            seg.type != SegmentType.FILE
            or not seg.file_id
            or (seg.file_kind_description and str(seg.file_kind_description).strip())
        ):
            out.append(seg)
            continue
        model = await file_repo.get_for_session(file_id=seg.file_id, session_id=session_id)
        if model is None:
            out.append(seg)
            continue
        desc = describe_user_upload_for_llm(
            model.content,
            mime_type=model.mime_type,
            filename=model.filename,
        )
        out.append(
            ContentSegment(
                type=SegmentType.FILE,
                file_id=seg.file_id,
                file_kind_description=desc,
            )
        )
    return tuple(out)


class GatewayIngressService:
    """Convert typed gateway inbound messages into session history and runtime events."""

    async def open_session(
        self,
        *,
        platform: str,
        title: str | None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Create one business session without gateway routing metadata."""
        raw_meta = dict(meta or {})
        requested_workspace = raw_meta.get("workspace_root")
        async with AsyncSessionLocal() as db:
            repo = SessionRepository(db)
            model = await repo.create(
                platform=platform,
                title=title,
                meta=raw_meta,
            )
            session_id = model.id
        workspace_root = requested_workspace if isinstance(requested_workspace, str) else None
        await get_session_runtime_store().initialize_session(
            session_id=session_id,
            requested_workspace_root=workspace_root,
        )
        return session_id

    async def submit_message(self, message: GatewayInboundMessage, *, bus: SessionEventBus) -> None:
        """Persist inbound message and publish follow-up runtime event when required."""
        async with AsyncSessionLocal() as db:
            session_repo = SessionRepository(db)
            session_model = await session_repo.get(message.session_id)
            if session_model is None:
                raise ValueError(f"Session not found: {message.session_id}")

            event_repo = EventRepository(db)
            memory = PersistentMemory(event_repo)
            if message.segments:
                enriched = await _enrich_inbound_file_segments(
                    message.session_id,
                    tuple(message.segments),
                    file_repo=FileRepository(db),
                )
                user_message = SessionMessageBlock.from_segments(
                    SessionMessageRole.USER,
                    enriched,
                )
                if user_message is None:
                    raise ValueError("Inbound segments are invalid")
                await memory.add_session_event(
                    session_id=message.session_id,
                    message=user_message,
                )

        if message.trigger_turn:
            await bus.publish(
                RuntimeEvent(
                    type=EventType.USER_MESSAGE_RECEIVED,
                    session_id=message.session_id,
                    payload={
                        "trigger_turn": True,
                        "task_type": message.task_type,
                        "enabled_groups": list(message.enabled_groups) if message.enabled_groups is not None else None,
                        "gateway_message_id": str(message.meta.get("message_id") or ""),
                        "adapter_key": message.adapter_key,
                    },
                    source=f"gateway:{message.adapter_key}",
                )
            )

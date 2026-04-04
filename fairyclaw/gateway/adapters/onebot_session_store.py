# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Adapter-local persistence for OneBot active sessions."""

from __future__ import annotations

from typing import Any

from fairyclaw.infrastructure.database.repository import OnebotSenderActiveRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal


class OnebotSessionStore:
    """Persist and load the active session for one OneBot sender."""

    async def get_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> str | None:
        async with AsyncSessionLocal() as db:
            repo = OnebotSenderActiveRepository(db)
            model = await repo.get(adapter_key=adapter_key, sender_ref=sender_ref)
            if model is None:
                return None
            return model.active_session_id

    async def set_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, Any], session_id: str) -> None:
        async with AsyncSessionLocal() as db:
            repo = OnebotSenderActiveRepository(db)
            await repo.upsert(adapter_key=adapter_key, sender_ref=sender_ref, active_session_id=session_id)

    async def clear_active_session_id(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> None:
        async with AsyncSessionLocal() as db:
            repo = OnebotSenderActiveRepository(db)
            await repo.delete(adapter_key=adapter_key, sender_ref=sender_ref)

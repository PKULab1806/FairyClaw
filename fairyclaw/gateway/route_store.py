# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Gateway session route persistence facade."""

from __future__ import annotations

from typing import Any

from fairyclaw.infrastructure.database.repository import GatewaySessionRouteRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal


class GatewaySessionRouteStore:
    """Persist and resolve gateway-side session routing state."""

    async def bind(
        self,
        *,
        session_id: str,
        adapter_key: str | None,
        sender_ref: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        async with AsyncSessionLocal() as db:
            repo = GatewaySessionRouteRepository(db)
            await repo.bind(
                session_id=session_id,
                adapter_key=adapter_key,
                sender_ref=sender_ref,
                parent_session_id=parent_session_id,
            )

    async def resolve(self, session_id: str) -> tuple[str, dict[str, Any]]:
        async with AsyncSessionLocal() as db:
            repo = GatewaySessionRouteRepository(db)
            model = await repo.resolve(session_id)
            if model is None or not model.adapter_key:
                raise ValueError(f"Missing gateway route for session: {session_id}")
            return model.adapter_key, dict(model.sender_ref or {})

    async def get_parent_session_id(self, session_id: str) -> str | None:
        async with AsyncSessionLocal() as db:
            repo = GatewaySessionRouteRepository(db)
            return await repo.get_parent_session_id(session_id)

    async def find_session_by_sender(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> str | None:
        async with AsyncSessionLocal() as db:
            repo = GatewaySessionRouteRepository(db)
            model = await repo.find_by_sender(adapter_key=adapter_key, sender_ref=sender_ref)
            if model is None:
                return None
            return model.session_id

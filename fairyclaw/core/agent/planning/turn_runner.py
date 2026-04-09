# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Planner turn execution helpers."""

from __future__ import annotations

import logging

from fairyclaw.core.agent.planning.planner import Planner
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.core.agent.types import TurnRequest
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def process_background_turn(
    request: TurnRequest,
    planner: Planner,
) -> None:
    """Run one planner turn in an isolated DB session context."""
    try:
        async with AsyncSessionLocal() as db:
            memory = PersistentMemory(EventRepository(db))
            await planner.process_turn(
                TurnRequest(
                    session_id=request.session_id,
                    user_segments=request.user_segments,
                    history_items=request.history_items,
                    memory=memory,
                    runtime=request.runtime,
                    session_kind=request.session_kind,
                )
            )
    except Exception:
        logger.exception("Background turn failed: session=%s", request.session_id)

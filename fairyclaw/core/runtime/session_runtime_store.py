# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Global session runtime context store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from fairyclaw.config import locations
from fairyclaw.infrastructure.database.repository import SessionRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal


@dataclass(frozen=True)
class SessionRuntimeContext:
    """Session-scoped runtime context shared across planner and tools."""

    session_id: str
    workspace_root: str
    short_session_id: str


class SessionRuntimeStore:
    """Global store for session runtime context."""

    def __init__(self) -> None:
        self._cache: dict[str, SessionRuntimeContext] = {}
        self._lock = asyncio.Lock()

    def _short_session_id(self, session_id: str) -> str:
        raw = str(session_id or "").strip()
        if raw.startswith("sess_"):
            raw = raw[5:]
        raw = raw.replace("-", "")
        return (raw[:8] or "unknown")

    def _default_workspace_for(self, session_id: str) -> str:
        root = locations.resolve_state_root() / "workspace" / self._short_session_id(session_id)
        return str(root.resolve())

    def _normalize_workspace_path(self, value: str) -> str:
        p = Path(value).expanduser()
        if not p.is_absolute():
            p = locations.path_anchor() / p
        return str(p.resolve())

    def _materialize_workspace(self, workspace_root: str) -> str:
        path = Path(workspace_root).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    async def _read_workspace_from_db(self, session_id: str) -> str | None:
        async with AsyncSessionLocal() as db:
            repo = SessionRepository(db)
            return await repo.get_workspace_root(session_id)

    async def _write_workspace_to_db(self, session_id: str, workspace_root: str) -> None:
        async with AsyncSessionLocal() as db:
            repo = SessionRepository(db)
            await repo.set_workspace_root(session_id, workspace_root)

    async def initialize_session(self, session_id: str, requested_workspace_root: str | None = None) -> SessionRuntimeContext:
        """Ensure session has workspace and return runtime context."""
        async with self._lock:
            workspace_raw = (requested_workspace_root or "").strip() or await self._read_workspace_from_db(session_id)
            workspace = self._normalize_workspace_path(workspace_raw) if workspace_raw else self._default_workspace_for(session_id)
            workspace = self._materialize_workspace(workspace)
            await self._write_workspace_to_db(session_id, workspace)
            context = SessionRuntimeContext(
                session_id=session_id,
                workspace_root=workspace,
                short_session_id=self._short_session_id(session_id),
            )
            self._cache[session_id] = context
            return context

    async def get(self, session_id: str) -> SessionRuntimeContext:
        """Return session runtime context, creating default workspace when missing."""
        cached = self._cache.get(session_id)
        if cached is not None:
            return cached
        return await self.initialize_session(session_id=session_id)


_runtime_store: SessionRuntimeStore | None = None


def get_session_runtime_store() -> SessionRuntimeStore:
    """Get process-global session runtime store singleton."""
    global _runtime_store
    if _runtime_store is None:
        _runtime_store = SessionRuntimeStore()
    return _runtime_store


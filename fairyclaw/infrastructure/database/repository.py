# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Database repository layer.

Encapsulates read/write operations for Session, Event, and File entities.
"""

import asyncio
import logging
from dataclasses import dataclass
import datetime as dt
from typing import Any, Awaitable, Callable, List

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from fairyclaw.config.settings import settings
from fairyclaw.infrastructure.database.models import (
    EventModel,
    FileModel,
    GatewaySessionRouteModel,
    MemoryCompactionModel,
    MessageRouteModel,
    TimerJobModel,
    OnebotSenderActiveModel,
    RagChunkModel,
    RagDocumentModel,
    SessionModel,
    utcnow,
)
from fairyclaw.core.domain import EventType

logger = logging.getLogger(__name__)


def _is_sqlite_locked_error(exc: Exception) -> bool:
    """Check whether exception indicates SQLite lock contention.

    Args:
        exc (Exception): Raised database exception.

    Returns:
        bool: True when message implies `database is locked`.
    """
    message = str(exc).lower()
    return "database is locked" in message


async def _write_with_retry(db: AsyncSession, write_op: Callable[[], Awaitable[None]]) -> None:
    """Execute write operation with retry policy for SQLite lock errors.

    Args:
        db (AsyncSession): Active database session.
        write_op (Callable[[], Awaitable[None]]): Async write callback mutating session state.

    Returns:
        None

    Raises:
        OperationalError: Re-raised when non-lock error occurs or retries are exhausted.
    """
    attempts = max(1, settings.db_write_retry_attempts)
    delay = max(0.0, settings.db_write_retry_base_delay_seconds)
    for attempt in range(attempts):
        try:
            await write_op()
            await db.commit()
            return
        except OperationalError as exc:
            await db.rollback()
            if not _is_sqlite_locked_error(exc) or attempt >= attempts - 1:
                raise
            wait_seconds = delay * (2 ** attempt)
            logger.warning(
                f"Database write retry due to lock: attempt={attempt + 1}/{attempts} wait={wait_seconds:.3f}s"
            )
            await asyncio.sleep(wait_seconds)


def _as_utc_datetime(value: dt.datetime | None) -> dt.datetime | None:
    """Normalize datetime to timezone-aware UTC for safe comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)

@dataclass
class SessionListItem:
    """Represent session summary row for list API response."""

    session_id: str
    title: str | None
    created_at: int
    last_activity_at: int
    event_count: int


@dataclass
class OnebotSessionListItem:
    """Represent one OneBot-owned session for session list output (e.g. `/sess ls`)."""

    session_id: str
    title: str | None
    updated_at: Any


def _normalize_sender_value(value: Any) -> str:
    raw = str(value or "").strip()
    return raw


def _normalize_onebot_sender_ref(sender_ref: dict[str, Any]) -> dict[str, str]:
    return {
        "platform": _normalize_sender_value(sender_ref.get("platform")) or "onebot",
        "user_id": _normalize_sender_value(sender_ref.get("user_id")),
        "group_id": _normalize_sender_value(sender_ref.get("group_id")),
        "self_id": _normalize_sender_value(sender_ref.get("self_id")),
    }


def _route_sender_lookup_values(sender_ref: dict[str, Any]) -> dict[str, str | None]:
    normalized = _normalize_onebot_sender_ref(sender_ref)
    return {
        "platform": normalized["platform"] or None,
        "user_id": normalized["user_id"] or None,
        "group_id": normalized["group_id"] or None,
        "self_id": normalized["self_id"] or None,
    }

class SessionRepository:
    """Repository for CRUD and summary operations on sessions."""

    def __init__(self, db: AsyncSession):
        """Initialize repository with request-scoped DB session.

        Args:
            db (AsyncSession): Active database session.

        Returns:
            None
        """
        self.db = db

    async def create(
        self,
        platform: str,
        title: str | None,
        meta: dict,
    ) -> SessionModel:
        """Create and persist one session record.

        Args:
            platform (str): Session origin platform identifier.
            title (str | None): Optional session title.
            meta (dict): Arbitrary metadata payload.

        Returns:
            SessionModel: Persisted session entity.
        """
        model = SessionModel(
            platform=platform,
            title=title,
            meta=meta or {},
        )
        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def get(self, session_id: str) -> SessionModel | None:
        """Fetch session by primary key.

        Args:
            session_id (str): Session identifier.

        Returns:
            SessionModel | None: Session entity or None when absent.
        """
        return await self.db.get(SessionModel, session_id)

    async def delete(self, session_id: str) -> bool:
        """Delete session by ID.

        Args:
            session_id (str): Session identifier.

        Returns:
            bool: True when deletion succeeds, False when session is missing.
        """
        model = await self.get(session_id)
        if not model:
            return False
        async def _write() -> None:
            await self.db.delete(model)

        await _write_with_retry(self.db, _write)
        return True

    async def update_activity(self, session_id: str) -> None:
        """Refresh `updated_at` timestamp of one session.

        Args:
            session_id (str): Session identifier.

        Returns:
            None
        """
        model = await self.get(session_id)
        if not model:
            return
        async def _write() -> None:
            model.updated_at = utcnow()

        await _write_with_retry(self.db, _write)

    async def _set_meta_value(self, session_id: str, key: str, value: Any) -> bool:
        """Set one session meta key and persist atomically (internal helper)."""
        model = await self.get(session_id)
        if not model:
            return False

        async def _write() -> None:
            meta = dict(model.meta or {})
            meta[str(key)] = value
            model.meta = meta
            model.updated_at = utcnow()

        await _write_with_retry(self.db, _write)
        return True

    async def _get_meta_value(self, session_id: str, key: str) -> Any | None:
        """Get one session meta key value (internal helper)."""
        model = await self.get(session_id)
        if not model:
            return None
        meta = dict(model.meta or {})
        return meta.get(str(key))

    async def set_workspace_root(self, session_id: str, workspace_root: str) -> bool:
        """Set session workspace root in session meta."""
        return await self._set_meta_value(session_id, "workspace_root", workspace_root)

    async def get_workspace_root(self, session_id: str) -> str | None:
        """Get session workspace root from session meta."""
        value = await self._get_meta_value(session_id, "workspace_root")
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    async def list_all(self) -> List[SessionListItem]:
        """List all sessions with aggregated event statistics.

        Returns:
            List[SessionListItem]: Ordered session summary list.
        """
        stmt = (
            select(
                SessionModel.id,
                SessionModel.title,
                SessionModel.created_at,
                func.coalesce(func.max(EventModel.timestamp), SessionModel.created_at),
                func.count(EventModel.id),
            )
            .outerjoin(EventModel, SessionModel.id == EventModel.session_id)
            .group_by(SessionModel.id)
            .order_by(SessionModel.updated_at.desc())
        )
        rows = (await self.db.execute(stmt)).all()
        items: List[SessionListItem] = []
        for row in rows:
            items.append(
                SessionListItem(
                    session_id=row[0],
                    title=row[1],
                    created_at=int(row[2].timestamp() * 1000),
                    last_activity_at=int(row[3].timestamp() * 1000),
                    event_count=int(row[4]),
                )
            )
        return items

class EventRepository:
    """Repository for session and operation event persistence."""

    def __init__(self, db: AsyncSession):
        """Initialize repository with request-scoped DB session.

        Args:
            db (AsyncSession): Active database session.

        Returns:
            None
        """
        self.db = db

    async def add_session_event(
        self,
        session_id: str,
        role: str,
        content: list[dict[str, Any]],
        *,
        usage_prompt_tokens: int | None = None,
        usage_completion_tokens: int | None = None,
        usage_total_tokens: int | None = None,
    ) -> EventModel:
        """Insert one user-visible session event.

        Args:
            session_id (str): Session identifier.
            role (str): Message role.
            content (list[dict[str, Any]]): Segment payload list.

        Returns:
            EventModel: Persisted event model.
        """
        model = EventModel(
            session_id=session_id,
            type=EventType.SESSION_EVENT.value,
            role=role,
            content=content,
            usage_prompt_tokens=usage_prompt_tokens,
            usage_completion_tokens=usage_completion_tokens,
            usage_total_tokens=usage_total_tokens,
        )
        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def add_operation_event(
        self,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: Any,
        *,
        usage_prompt_tokens: int | None = None,
        usage_completion_tokens: int | None = None,
        usage_total_tokens: int | None = None,
    ) -> EventModel:
        """Insert one tool-operation event.

        Args:
            session_id (str): Session identifier.
            tool_name (str): Executed tool name.
            tool_args (dict[str, Any]): Tool argument payload.
            tool_result (Any): Tool result payload.

        Returns:
            EventModel: Persisted event model.
        """
        model = EventModel(
            session_id=session_id,
            type=EventType.OPERATION_EVENT.value,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            usage_prompt_tokens=usage_prompt_tokens,
            usage_completion_tokens=usage_completion_tokens,
            usage_total_tokens=usage_total_tokens,
        )
        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def history(self, session_id: str, limit: int = 50) -> List[EventModel]:
        """Read latest session events in chronological order.

        Args:
            session_id (str): Session identifier.
            limit (int): Maximum events to fetch.

        Returns:
            List[EventModel]: Ordered event list from oldest to newest.
        """
        stmt = (
            select(EventModel)
            .where(EventModel.session_id == session_id)
            .order_by(EventModel.timestamp.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(reversed(rows))

    async def session_event_stats(self, session_id: str) -> tuple[int, int | None]:
        """Return persisted event count and last event time in ms for one session."""
        stmt = select(func.count(EventModel.id), func.max(EventModel.timestamp)).where(
            EventModel.session_id == session_id
        )
        row = (await self.db.execute(stmt)).one()
        count = int(row[0] or 0)
        ts = row[1]
        if ts is None:
            return count, None
        return count, int(ts.timestamp() * 1000)

    async def usage_totals(
        self,
        *,
        session_id: str | None = None,
        session_ids: list[str] | None = None,
        month_utc: dt.datetime | None = None,
    ) -> dict[str, int]:
        """Aggregate persisted token usage totals."""
        stmt = select(
            func.coalesce(func.sum(EventModel.usage_prompt_tokens), 0),
            func.coalesce(func.sum(EventModel.usage_completion_tokens), 0),
            func.coalesce(func.sum(EventModel.usage_total_tokens), 0),
        )
        if session_id is not None:
            stmt = stmt.where(EventModel.session_id == session_id)
        if session_ids is not None:
            normalized = [sid for sid in session_ids if sid]
            if not normalized:
                return {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
            stmt = stmt.where(EventModel.session_id.in_(normalized))
        if month_utc is not None:
            month_start = dt.datetime(month_utc.year, month_utc.month, 1, tzinfo=dt.timezone.utc)
            if month_utc.month == 12:
                month_end = dt.datetime(month_utc.year + 1, 1, 1, tzinfo=dt.timezone.utc)
            else:
                month_end = dt.datetime(month_utc.year, month_utc.month + 1, 1, tzinfo=dt.timezone.utc)
            stmt = stmt.where(EventModel.timestamp >= month_start).where(EventModel.timestamp < month_end)
        row = (await self.db.execute(stmt)).one()
        return {
            "prompt_tokens": int(row[0] or 0),
            "completion_tokens": int(row[1] or 0),
            "total_tokens": int(row[2] or 0),
        }


def _normalize_file_id_for_lookup(file_id: str) -> str:
    """Normalize file primary keys: stored as file_ + lowercase uuid4 hex.

    LLMs and some clients echo mixed-case hex; PK lookups are case-sensitive.
    """
    if not file_id.startswith("file_"):
        return file_id
    return "file_" + file_id[5:].lower()


class FileRepository:
    """Repository for session file persistence and lookup."""

    def __init__(self, db: AsyncSession):
        """Initialize repository with request-scoped DB session.

        Args:
            db (AsyncSession): Active database session.

        Returns:
            None
        """
        self.db = db

    async def create(self, session_id: str, filename: str, content: bytes, mime_type: str | None = None) -> FileModel:
        """Create and persist session file record.

        Args:
            session_id (str): Owning session identifier.
            filename (str): Original file name.
            content (bytes): Binary file payload.
            mime_type (str | None): Optional MIME type.

        Returns:
            FileModel: Persisted file entity.
        """
        model = FileModel(
            session_id=session_id,
            filename=filename,
            content=content,
            size=len(content),
            mime_type=mime_type,
        )
        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def get(self, file_id: str) -> FileModel | None:
        """Fetch file by primary key.

        Args:
            file_id (str): File identifier.

        Returns:
            FileModel | None: File model or None.
        """
        canon = _normalize_file_id_for_lookup(file_id)
        return await self.db.get(FileModel, canon)

    async def get_for_session(self, file_id: str, session_id: str) -> FileModel | None:
        """Fetch file scoped to session for isolation checks.

        Args:
            file_id (str): File identifier.
            session_id (str): Session identifier.

        Returns:
            FileModel | None: File model when it belongs to session.
        """
        canon = _normalize_file_id_for_lookup(file_id)
        stmt = select(FileModel).where(FileModel.id == canon, FileModel.session_id == session_id)
        return (await self.db.execute(stmt)).scalars().first()

    async def list_by_session(self, session_id: str) -> List[FileModel]:
        """List all file records belonging to one session.

        Args:
            session_id (str): Session identifier.

        Returns:
            List[FileModel]: File model list.
        """
        stmt = select(FileModel).where(FileModel.session_id == session_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def clone_to_session(self, *, file_id: str, source_session_id: str, target_session_id: str) -> FileModel | None:
        """Clone one existing session file into another session."""
        source = await self.get_for_session(file_id=file_id, session_id=source_session_id)
        if source is None:
            return None
        return await self.create(
            session_id=target_session_id,
            filename=source.filename,
            content=source.content,
            mime_type=source.mime_type,
        )


class OnebotSenderActiveRepository:
    """Repository for OneBot sender -> active session mapping."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _pk_tuple(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> tuple[str, str, str, str, str]:
        normalized = _normalize_onebot_sender_ref(sender_ref)
        return (
            _normalize_sender_value(adapter_key) or "onebot",
            normalized["platform"],
            normalized["user_id"],
            normalized["group_id"],
            normalized["self_id"],
        )

    async def get(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> OnebotSenderActiveModel | None:
        return await self.db.get(OnebotSenderActiveModel, self._pk_tuple(adapter_key=adapter_key, sender_ref=sender_ref))

    async def upsert(self, *, adapter_key: str, sender_ref: dict[str, Any], active_session_id: str) -> OnebotSenderActiveModel:
        model = await self.get(adapter_key=adapter_key, sender_ref=sender_ref)
        pk_tuple = self._pk_tuple(adapter_key=adapter_key, sender_ref=sender_ref)
        if model is None:
            model = OnebotSenderActiveModel(
                adapter_key=pk_tuple[0],
                sender_platform=pk_tuple[1],
                sender_user_id=pk_tuple[2],
                sender_group_id=pk_tuple[3],
                sender_self_id=pk_tuple[4],
                active_session_id=active_session_id,
            )

        async def _write() -> None:
            self.db.add(model)
            model.active_session_id = active_session_id
            model.updated_at = utcnow()

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def delete(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> None:
        model = await self.get(adapter_key=adapter_key, sender_ref=sender_ref)
        if model is None:
            return

        async def _write() -> None:
            await self.db.delete(model)

        await _write_with_retry(self.db, _write)


class GatewaySessionRouteRepository:
    """Repository for persisted gateway session routing state."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, session_id: str) -> GatewaySessionRouteModel | None:
        return await self.db.get(GatewaySessionRouteModel, session_id)

    async def bind(
        self,
        *,
        session_id: str,
        adapter_key: str | None,
        sender_ref: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
    ) -> GatewaySessionRouteModel:
        sender = dict(sender_ref or {})
        model = await self.get(session_id)
        if model is None:
            model = GatewaySessionRouteModel(session_id=session_id)

        def _normalize_sender(field: str) -> str | None:
            raw = sender.get(field)
            if raw is None:
                return None
            value = str(raw).strip()
            return value or None

        async def _write() -> None:
            self.db.add(model)
            if adapter_key is not None:
                normalized_adapter_key = adapter_key.strip()
                model.adapter_key = normalized_adapter_key or None
            model.sender_ref = sender
            model.sender_platform = _normalize_sender("platform")
            model.sender_user_id = _normalize_sender("user_id")
            model.sender_group_id = _normalize_sender("group_id")
            model.sender_self_id = _normalize_sender("self_id")
            if parent_session_id is not None:
                normalized_parent = parent_session_id.strip()
                model.parent_session_id = normalized_parent or None
            model.updated_at = utcnow()

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def resolve(self, session_id: str) -> GatewaySessionRouteModel | None:
        current_id = session_id
        visited: set[str] = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            model = await self.get(current_id)
            if model is None:
                return None
            if model.adapter_key:
                return model
            current_id = model.parent_session_id or ""
        return None

    async def get_parent_session_id(self, session_id: str) -> str | None:
        model = await self.get(session_id)
        if model is None or not model.parent_session_id:
            return None
        return model.parent_session_id

    async def list_by_parent_session(self, parent_session_id: str) -> list[GatewaySessionRouteModel]:
        stmt = select(GatewaySessionRouteModel).where(GatewaySessionRouteModel.parent_session_id == parent_session_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def find_by_sender(
        self,
        *,
        adapter_key: str,
        sender_ref: dict[str, Any],
    ) -> GatewaySessionRouteModel | None:
        values = _route_sender_lookup_values(sender_ref)
        stmt = select(GatewaySessionRouteModel).where(
            GatewaySessionRouteModel.adapter_key == adapter_key,
            GatewaySessionRouteModel.sender_platform == values["platform"],
            GatewaySessionRouteModel.sender_user_id == values["user_id"],
            GatewaySessionRouteModel.sender_group_id == values["group_id"],
            GatewaySessionRouteModel.sender_self_id == values["self_id"],
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def get_for_onebot_sender(self, *, session_id: str, sender_ref: dict[str, Any]) -> GatewaySessionRouteModel | None:
        values = _route_sender_lookup_values(sender_ref)
        stmt = select(GatewaySessionRouteModel).where(
            GatewaySessionRouteModel.session_id == session_id,
            GatewaySessionRouteModel.adapter_key == "onebot",
            GatewaySessionRouteModel.sender_platform == values["platform"],
            GatewaySessionRouteModel.sender_user_id == values["user_id"],
            GatewaySessionRouteModel.sender_group_id == values["group_id"],
            GatewaySessionRouteModel.sender_self_id == values["self_id"],
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def list_sessions_for_onebot_sender(self, *, sender_ref: dict[str, Any]) -> list[OnebotSessionListItem]:
        values = _route_sender_lookup_values(sender_ref)
        stmt = (
            select(GatewaySessionRouteModel.session_id, SessionModel.title, SessionModel.updated_at)
            .join(SessionModel, SessionModel.id == GatewaySessionRouteModel.session_id)
            .where(
                GatewaySessionRouteModel.adapter_key == "onebot",
                GatewaySessionRouteModel.sender_platform == values["platform"],
                GatewaySessionRouteModel.sender_user_id == values["user_id"],
                GatewaySessionRouteModel.sender_group_id == values["group_id"],
                GatewaySessionRouteModel.sender_self_id == values["self_id"],
            )
            .order_by(SessionModel.updated_at.desc())
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            OnebotSessionListItem(
                session_id=row[0],
                title=row[1],
                updated_at=row[2],
            )
            for row in rows
        ]


class RagRepository:
    """Repository for RAG documents and chunks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(
        self,
        session_id: str,
        source_type: str,
        source_ref: str,
        status: str = "pending",
        meta: dict[str, Any] | None = None,
    ) -> RagDocumentModel:
        model = RagDocumentModel(
            session_id=session_id,
            source_type=source_type,
            source_ref=source_ref,
            status=status,
            meta=meta or {},
        )

        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def set_document_status(self, document_id: str, status: str) -> None:
        model = await self.db.get(RagDocumentModel, document_id)
        if not model:
            return

        async def _write() -> None:
            model.status = status

        await _write_with_retry(self.db, _write)

    async def insert_chunks(
        self,
        document_id: str,
        session_id: str,
        chunks: list[dict[str, Any]],
    ) -> list[RagChunkModel]:
        models: list[RagChunkModel] = []
        for idx, chunk in enumerate(chunks):
            models.append(
                RagChunkModel(
                    document_id=document_id,
                    session_id=session_id,
                    chunk_index=int(chunk.get("chunk_index", idx)),
                    text=str(chunk.get("text") or ""),
                    token_count=int(chunk.get("token_count") or 0),
                    embedding=chunk.get("embedding"),
                    meta=chunk.get("meta") or {},
                )
            )

        async def _write() -> None:
            self.db.add_all(models)

        await _write_with_retry(self.db, _write)
        for model in models:
            await self.db.refresh(model)
        return models

    async def query_chunks(self, session_id: str, top_k: int = 5) -> list[RagChunkModel]:
        stmt = (
            select(RagChunkModel)
            .where(RagChunkModel.session_id == session_id)
            .order_by(RagChunkModel.created_at.desc())
            .limit(top_k)
        )
        return list((await self.db.execute(stmt)).scalars().all())


class MemoryCompactionRepository:
    """Repository for memory compaction snapshots."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_snapshot(
        self,
        session_id: str,
        strategy: str,
        summary_text: str,
        key_facts: dict[str, Any] | None = None,
        from_event_id: str | None = None,
        to_event_id: str | None = None,
        created_by: str = "auto",
    ) -> MemoryCompactionModel:
        model = MemoryCompactionModel(
            session_id=session_id,
            strategy=strategy,
            from_event_id=from_event_id,
            to_event_id=to_event_id,
            summary_text=summary_text,
            key_facts=key_facts or {},
            created_by=created_by,
        )

        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def latest_snapshot(self, session_id: str) -> MemoryCompactionModel | None:
        stmt = (
            select(MemoryCompactionModel)
            .where(MemoryCompactionModel.session_id == session_id)
            .order_by(MemoryCompactionModel.created_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()


class RouteRepository:
    """Repository for message route decisions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_route_decision(
        self,
        session_id: str,
        route_type: str,
        decision: dict[str, Any],
        input_event_id: str | None = None,
        reason: str | None = None,
    ) -> MessageRouteModel:
        model = MessageRouteModel(
            session_id=session_id,
            input_event_id=input_event_id,
            route_type=route_type,
            decision=decision,
            reason=reason,
        )

        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def recent_routes(self, session_id: str, limit: int = 20) -> list[MessageRouteModel]:
        stmt = (
            select(MessageRouteModel)
            .where(MessageRouteModel.session_id == session_id)
            .order_by(MessageRouteModel.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())


class TimerJobRepository:
    """Repository for timer runtime jobs (heartbeat/cron)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        owner_session_id: str,
        creator_session_id: str,
        mode: str,
        next_fire_at: dt.datetime,
        payload: str | None = None,
        cron_expr: str | None = None,
        interval_seconds: int | None = None,
        deadline_at: dt.datetime | None = None,
        max_runs: int | None = None,
    ) -> TimerJobModel:
        model = TimerJobModel(
            owner_session_id=owner_session_id,
            creator_session_id=creator_session_id,
            mode=mode,
            cron_expr=cron_expr,
            interval_seconds=interval_seconds,
            payload=str(payload or ""),
            status="pending",
            next_fire_at=next_fire_at,
            deadline_at=deadline_at,
            max_runs=max_runs,
            run_count=0,
            failure_count=0,
            last_error=None,
            claimed_by=None,
            claimed_at=None,
            active=True,
        )

        async def _write() -> None:
            self.db.add(model)

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def get(self, job_id: str) -> TimerJobModel | None:
        return await self.db.get(TimerJobModel, job_id)

    async def list_jobs(
        self,
        *,
        owner_session_id: str,
        creator_session_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[TimerJobModel]:
        stmt = select(TimerJobModel).where(TimerJobModel.owner_session_id == owner_session_id)
        if creator_session_id:
            stmt = stmt.where(TimerJobModel.creator_session_id == creator_session_id)
        if statuses:
            normalized = [s for s in statuses if isinstance(s, str) and s.strip()]
            if normalized:
                stmt = stmt.where(TimerJobModel.status.in_(normalized))
        stmt = stmt.order_by(TimerJobModel.created_at.desc()).limit(max(1, int(limit)))
        return list((await self.db.execute(stmt)).scalars().all())

    async def claim_due_jobs(
        self,
        *,
        now: dt.datetime,
        worker_id: str,
        limit: int = 20,
        stale_claim_after_seconds: int = 120,
    ) -> list[TimerJobModel]:
        stale_at = now - dt.timedelta(seconds=max(1, stale_claim_after_seconds))
        stmt = (
            select(TimerJobModel)
            .where(
                and_(
                    TimerJobModel.active.is_(True),
                    TimerJobModel.status.in_(["pending", "running"]),
                    TimerJobModel.next_fire_at <= now,
                    or_(
                        TimerJobModel.claimed_at.is_(None),
                        TimerJobModel.claimed_at <= stale_at,
                    ),
                )
            )
            .order_by(TimerJobModel.next_fire_at.asc())
            .limit(max(1, int(limit)))
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        if not rows:
            return []

        async def _write() -> None:
            for row in rows:
                row.claimed_by = worker_id
                row.claimed_at = now
                if row.status == "pending":
                    row.status = "running"
                row.updated_at = utcnow()

        await _write_with_retry(self.db, _write)
        for row in rows:
            await self.db.refresh(row)
        return rows

    async def update_after_run(
        self,
        *,
        job_id: str,
        now: dt.datetime,
        next_fire_at: dt.datetime | None,
        success: bool,
        terminal_status: str | None = None,
        last_error: str | None = None,
    ) -> TimerJobModel | None:
        model = await self.get(job_id)
        if model is None:
            return None

        async def _write() -> None:
            model.run_count = int(model.run_count or 0) + 1
            if success:
                model.failure_count = 0
            else:
                model.failure_count = int(model.failure_count or 0) + 1
            model.last_error = (last_error or "").strip() or None
            model.claimed_by = None
            model.claimed_at = None

            if terminal_status:
                model.status = terminal_status
                model.active = False
            elif next_fire_at is None:
                model.status = "completed"
                model.active = False
            else:
                model.status = "running"
                model.next_fire_at = next_fire_at

            deadline_at = _as_utc_datetime(model.deadline_at)
            now_utc = _as_utc_datetime(now) or now
            if deadline_at is not None and deadline_at <= now_utc and model.active:
                model.status = "completed"
                model.active = False
            if model.max_runs is not None and int(model.run_count or 0) >= int(model.max_runs) and model.active:
                model.status = "completed"
                model.active = False
            model.updated_at = utcnow()

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

    async def cancel(self, *, job_id: str) -> TimerJobModel | None:
        model = await self.get(job_id)
        if model is None:
            return None

        async def _write() -> None:
            model.status = "cancelled"
            model.active = False
            model.claimed_by = None
            model.claimed_at = None
            model.updated_at = utcnow()

        await _write_with_retry(self.db, _write)
        await self.db.refresh(model)
        return model

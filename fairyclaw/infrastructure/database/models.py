# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Database ORM model definitions."""

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

def utcnow() -> dt.datetime:
    """Return timezone-aware current UTC timestamp.

    Returns:
        dt.datetime: UTC datetime with timezone info.
    """
    return dt.datetime.now(dt.timezone.utc)

class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM entities."""

    pass

class SessionModel(Base):
    """Persisted session aggregate entity."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"sess_{uuid.uuid4().hex}")
    platform: Mapped[str] = mapped_column(String(64), default="web")
    status: Mapped[str] = mapped_column(String(32), default="active")
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["EventModel"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    files: Mapped[list["FileModel"]] = relationship(back_populates="session", cascade="all, delete-orphan")

class EventModel(Base):
    """Persisted session event entity (message/tool operation)."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"evt_{uuid.uuid4().hex}")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session: Mapped[SessionModel] = relationship(back_populates="events")

class FileModel(Base):
    """Persisted binary file entity bound to a session."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: f"file_{uuid.uuid4().hex[:12]}",
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[SessionModel] = relationship(back_populates="files")


class GatewaySessionRouteModel(Base):
    """Persisted gateway-side session routing and parent-session mapping."""

    __tablename__ = "gateway_session_routes"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    adapter_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sender_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    sender_platform: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sender_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sender_group_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sender_self_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    parent_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OnebotSenderActiveModel(Base):
    """Persist the currently selected OneBot session for one sender identity."""

    __tablename__ = "onebot_sender_active"

    adapter_key: Mapped[str] = mapped_column(String(64), primary_key=True, default="onebot")
    sender_platform: Mapped[str] = mapped_column(String(64), primary_key=True, default="onebot")
    sender_user_id: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    sender_group_id: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    sender_self_id: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    active_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RagDocumentModel(Base):
    """Indexed source document metadata for RAG."""

    __tablename__ = "rag_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"ragdoc_{uuid.uuid4().hex}")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="file")
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RagChunkModel(Base):
    """Chunk-level index payload for retrieval."""

    __tablename__ = "rag_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"ragchunk_{uuid.uuid4().hex}")
    document_id: Mapped[str] = mapped_column(ForeignKey("rag_documents.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String, default="")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryCompactionModel(Base):
    """Compacted memory snapshot records."""

    __tablename__ = "memory_compactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"memc_{uuid.uuid4().hex}")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(64), default="summary")
    from_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_text: Mapped[str] = mapped_column(String, default="")
    key_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(32), default="auto")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageRouteModel(Base):
    """Message routing decision audit records."""

    __tablename__ = "message_routes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"route_{uuid.uuid4().hex}")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    input_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    route_type: Mapped[str] = mapped_column(String(64), default="tool_group")
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TimerJobModel(Base):
    """Persisted timer job records for heartbeat/cron runtime."""

    __tablename__ = "timer_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"timer_{uuid.uuid4().hex[:24]}")
    owner_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    creator_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="heartbeat")
    cron_expr: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    next_fire_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    deadline_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    max_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

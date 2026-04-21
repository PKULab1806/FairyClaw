# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Typed WebSocket bridge protocol models."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from fairyclaw.core.domain import ContentSegment

PROTOCOL_VERSION = 2

FRAME_HELLO = "hello"
FRAME_HELLO_ACK = "hello_ack"
FRAME_RESUME = "resume"
FRAME_SESSION_OPEN = "session_open"
FRAME_SESSION_OPEN_ACK = "session_open_ack"
FRAME_INBOUND = "inbound"
FRAME_OUTBOUND = "outbound"
FRAME_ACK = "ack"
FRAME_ERROR = "error"
FRAME_HEARTBEAT = "heartbeat"

FRAME_FILE_PUT_INIT = "file_put_init"
FRAME_FILE_PUT_CHUNK = "file_put_chunk"
FRAME_FILE_PUT_COMMIT = "file_put_commit"
FRAME_FILE_PUT_ACK = "file_put_ack"
FRAME_FILE_GET = "file_get"
FRAME_FILE_GET_CHUNK = "file_get_chunk"
FRAME_FILE_GET_ACK = "file_get_ack"

ACK_STATUS_OK = "ok"
ACK_STATUS_FAILED = "failed"
ACK_STATUS_DUPLICATE = "duplicate"
ACK_STATUS_INVALID = "invalid"

OUTBOUND_KIND_TEXT = "text"
OUTBOUND_KIND_FILE = "file"
OUTBOUND_KIND_SEGMENTS = "segments"
OUTBOUND_KIND_EVENT = "event"

# Sentinel session_id for outbound events that must reach every connected web client (e.g. TelemetrySnapshot).
OUTBOUND_BROADCAST_SESSION_ID = "__fc_broadcast__"

FRAME_GATEWAY_CONTROL = "gateway_control"
FRAME_GATEWAY_CONTROL_ACK = "gateway_control_ack"


def now_ms() -> int:
    """Return current timestamp in milliseconds."""
    return int(time.time() * 1000)


def new_frame_id(prefix: str = "frm") -> str:
    """Build a stable frame identifier."""
    return f"{prefix}_{uuid.uuid4().hex}"


def sha256_hex(data: bytes) -> str:
    """Return SHA-256 hex digest for one bytes payload."""
    return hashlib.sha256(data).hexdigest()


def _to_json_compatible(value: Any) -> Any:
    """Recursively convert dataclasses and segments into JSON-compatible values."""
    if isinstance(value, ContentSegment):
        return value.to_dict()
    if is_dataclass(value):
        return _to_json_compatible(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_compatible(item) for item in value]
    return value


@dataclass(frozen=True)
class BridgeFrame:
    """One WebSocket bridge frame."""

    type: str
    payload: dict[str, Any]
    id: str = field(default_factory=new_frame_id)
    ts_ms: int = field(default_factory=now_ms)
    v: int = PROTOCOL_VERSION
    trace: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert frame into a JSON-compatible dictionary."""
        data: dict[str, Any] = {
            "v": self.v,
            "type": self.type,
            "id": self.id,
            "ts_ms": self.ts_ms,
            "payload": _to_json_compatible(self.payload),
        }
        if self.trace is not None:
            data["trace"] = _to_json_compatible(self.trace)
        return data

    def to_json(self) -> str:
        """Serialize frame to JSON text."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeFrame":
        """Parse frame from dictionary payload."""
        return cls(
            v=int(data.get("v") or PROTOCOL_VERSION),
            type=str(data.get("type") or ""),
            id=str(data.get("id") or new_frame_id()),
            ts_ms=int(data.get("ts_ms") or now_ms()),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
            trace=data.get("trace") if isinstance(data.get("trace"), dict) else None,
        )

    @classmethod
    def from_json(cls, raw: str) -> "BridgeFrame":
        """Parse frame from JSON text."""
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class GatewayAdapterDescriptor:
    """One adapter descriptor declared in hello payload."""

    adapter_key: str
    kind: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_key": self.adapter_key,
            "kind": self.kind,
            "version": self.version,
        }


@dataclass(frozen=True)
class HelloPayload:
    """Initial gateway hello payload."""

    gateway_id: str
    token: str
    adapters: tuple[GatewayAdapterDescriptor, ...]
    supports: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gateway_id": self.gateway_id,
            "token": self.token,
            "adapters": [item.to_dict() for item in self.adapters],
            "supports": dict(self.supports),
        }


@dataclass(frozen=True)
class HelloAckPayload:
    """Business hello acknowledgement payload."""

    ok: bool
    connection_id: str
    limits: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "connection_id": self.connection_id,
            "limits": dict(self.limits),
        }
        if self.error is not None:
            data["error"] = dict(self.error)
        return data


@dataclass(frozen=True)
class ResumePayload:
    """Reconnect resume payload."""

    gateway_id: str
    last_ack_inbound_id: str | None = None
    last_ack_outbound_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gateway_id": self.gateway_id,
            "last_ack_inbound_id": self.last_ack_inbound_id,
            "last_ack_outbound_id": self.last_ack_outbound_id,
        }


@dataclass(frozen=True)
class SessionOpenPayload:
    """Open one business session through the bridge."""

    adapter_key: str
    platform: str
    title: str | None = None
    # Optional metadata; supports `workspace_root` for session workspace initialization.
    meta: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_key": self.adapter_key,
            "platform": self.platform,
            "title": self.title,
            "meta": dict(self.meta),
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class SessionOpenAckPayload:
    """Session open acknowledgement payload."""

    ok: bool
    session_id: str | None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "session_id": self.session_id,
        }
        if self.error is not None:
            data["error"] = dict(self.error)
        return data


@dataclass(frozen=True)
class GatewaySenderRef:
    """External sender identity bound to one inbound message."""

    platform: str | None = None
    user_id: str | None = None
    group_id: str | None = None
    self_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        data: dict[str, str] = {}
        if self.platform is not None:
            data["platform"] = self.platform
        if self.user_id is not None:
            data["user_id"] = self.user_id
        if self.group_id is not None:
            data["group_id"] = self.group_id
        if self.self_id is not None:
            data["self_id"] = self.self_id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "GatewaySenderRef | None":
        if not isinstance(data, dict):
            return None
        return cls(
            platform=str(data.get("platform")) if data.get("platform") is not None else None,
            user_id=str(data.get("user_id")) if data.get("user_id") is not None else None,
            group_id=str(data.get("group_id")) if data.get("group_id") is not None else None,
            self_id=str(data.get("self_id")) if data.get("self_id") is not None else None,
        )


@dataclass(frozen=True)
class GatewayInboundMessage:
    """One gateway inbound payload."""

    session_id: str
    adapter_key: str
    segments: tuple[ContentSegment, ...]
    trigger_turn: bool
    sender: GatewaySenderRef | None = None
    task_type: str | None = None
    enabled_groups: tuple[str, ...] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "adapter_key": self.adapter_key,
            "sender": self.sender.to_dict() if self.sender is not None else None,
            "segments": [segment.to_dict() for segment in self.segments],
            "trigger_turn": self.trigger_turn,
            "task_type": self.task_type,
            "enabled_groups": list(self.enabled_groups) if self.enabled_groups is not None else None,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "GatewayInboundMessage":
        raw_segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
        segments = tuple(
            ContentSegment.from_dict(item)
            for item in raw_segments
            if isinstance(item, dict) and item.get("type")
        )
        raw_groups = payload.get("enabled_groups")
        enabled_groups = (
            tuple(group for group in raw_groups if isinstance(group, str) and group.strip())
            if isinstance(raw_groups, list)
            else None
        )
        return cls(
            session_id=str(payload.get("session_id") or ""),
            adapter_key=str(payload.get("adapter_key") or ""),
            segments=segments,
            trigger_turn=bool(payload.get("trigger_turn")),
            sender=GatewaySenderRef.from_dict(payload.get("sender")),
            task_type=str(payload.get("task_type")).strip() if isinstance(payload.get("task_type"), str) else None,
            enabled_groups=enabled_groups,
            meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        )


@dataclass(frozen=True)
class GatewayOutboundMessage:
    """One gateway outbound payload.

    ``session_id`` is the session where the payload originated (main web session or child
    sub-agent session). The Web gateway may deliver to parent-bound sockets when the child
    id has no subscribers; see GATEWAY_RUNTIME_PROTOCOL.md (Sub-session push routing).

    Reserved ``meta`` keys (Web gateway / sub-session routing):

    - ``fc_parent_session_id`` (str): set when the Web adapter delivers a child-session
      outbound via the parent's bound WebSocket subscribers.
    """

    session_id: str
    kind: str
    content: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
    # Optional routing hints: filled by the business bridge when the gateway DB has no row
    # (e.g. split processes/databases) so the gateway can still dispatch and adapters can resolve senders.
    adapter_key: str | None = None
    sender_ref: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "kind": self.kind,
            "content": dict(self.content),
            "meta": dict(self.meta),
        }
        if self.adapter_key is not None:
            payload["adapter_key"] = self.adapter_key
        if self.sender_ref is not None:
            payload["sender_ref"] = dict(self.sender_ref)
        return payload

    @classmethod
    def text(cls, session_id: str, text: str, meta: dict[str, Any] | None = None) -> "GatewayOutboundMessage":
        return cls(
            session_id=session_id,
            kind=OUTBOUND_KIND_TEXT,
            content={"text": text},
            meta=dict(meta or {}),
        )

    @classmethod
    def file(
        cls,
        session_id: str,
        file_id: str,
        meta: dict[str, Any] | None = None,
    ) -> "GatewayOutboundMessage":
        return cls(
            session_id=session_id,
            kind=OUTBOUND_KIND_FILE,
            content={"file_id": file_id},
            meta=dict(meta or {}),
        )

    @classmethod
    def event(
        cls,
        session_id: str,
        *,
        event_type: str,
        content: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> "GatewayOutboundMessage":
        """Build one outbound event (telemetry, tool_call/tool_result, session list, etc.)."""
        body = dict(content)
        body["event_type"] = event_type
        return cls(
            session_id=session_id,
            kind=OUTBOUND_KIND_EVENT,
            content=body,
            meta=dict(meta or {}),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "GatewayOutboundMessage":
        ak_raw = payload.get("adapter_key")
        adapter_key = str(ak_raw).strip() if isinstance(ak_raw, str) and str(ak_raw).strip() else None
        sr_raw = payload.get("sender_ref")
        sender_ref = dict(sr_raw) if isinstance(sr_raw, dict) else None
        return cls(
            session_id=str(payload.get("session_id") or ""),
            kind=str(payload.get("kind") or ""),
            content=payload.get("content") if isinstance(payload.get("content"), dict) else {},
            meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
            adapter_key=adapter_key,
            sender_ref=sender_ref,
        )


@dataclass(frozen=True)
class AckPayload:
    """Acknowledgement payload for one frame."""

    ref_type: str
    ref_id: str
    status: str
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
            "status": self.status,
        }
        if self.error is not None:
            data["error"] = dict(self.error)
        return data


@dataclass(frozen=True)
class ErrorPayload:
    """Protocol error payload."""

    code: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            data["details"] = dict(self.details)
        return data


@dataclass(frozen=True)
class HeartbeatPayload:
    """Heartbeat payload."""

    seq: int

    def to_dict(self) -> dict[str, int]:
        return {"seq": self.seq}


@dataclass(frozen=True)
class GatewayFilePutInit:
    """Initialize one file upload."""

    session_id: str
    adapter_key: str
    message_id: str
    filename: str
    size_bytes: int
    sha256_hex: str
    mime_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "adapter_key": self.adapter_key,
            "message_id": self.message_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256_hex": self.sha256_hex,
        }


@dataclass(frozen=True)
class GatewayFilePutChunk:
    """One file upload chunk."""

    upload_id: str
    seq: int
    data_b64: str
    chunk_bytes: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "seq": self.seq,
            "data_b64": self.data_b64,
            "chunk_bytes": self.chunk_bytes,
        }


@dataclass(frozen=True)
class GatewayFilePutCommit:
    """Commit one file upload."""

    upload_id: str
    total_chunks: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "total_chunks": self.total_chunks,
        }


@dataclass(frozen=True)
class GatewayFilePutAck:
    """Upload acknowledgement payload."""

    status: str
    upload_id: str
    seq: int | None = None
    file_id: str | None = None
    error: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "upload_id": self.upload_id,
            "seq": self.seq,
            "file_id": self.file_id,
            "error": dict(self.error) if self.error is not None else None,
        }


@dataclass(frozen=True)
class GatewayFileGetRequest:
    """Request one file download."""

    session_id: str
    file_id: str
    request_id: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "file_id": self.file_id,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class GatewayFileGetChunk:
    """One file download chunk."""

    request_id: str
    file_id: str
    seq: int
    data_b64: str
    chunk_bytes: int
    is_last: bool
    filename: str | None = None
    mime_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "file_id": self.file_id,
            "seq": self.seq,
            "data_b64": self.data_b64,
            "chunk_bytes": self.chunk_bytes,
            "is_last": self.is_last,
            "filename": self.filename,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class GatewayFileGetAck:
    """File download acknowledgement."""

    request_id: str
    file_id: str
    status: str
    error: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "file_id": self.file_id,
            "status": self.status,
            "error": dict(self.error) if self.error is not None else None,
        }

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session-level runtime event bus.

Defines event models and dispatch logic, ensuring:
1. In-order handling within the same session;
2. Concurrent handling across different sessions;
3. Recoverable execution based on mailbox and heartbeat state.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import NewType

from fairyclaw.core.agent.hooks.protocol import JsonObject
from fairyclaw.core.events.payloads import normalize_trigger_turn
logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Enumerate runtime event categories used in scheduler pipeline."""

    USER_MESSAGE_RECEIVED = "user_message_received"
    SUBTASK_COMPLETED = "subtask_completed"
    WAKEUP_REQUESTED = "wakeup_requested"
    FILE_UPLOAD_RECEIVED = "file_upload_received"
    FORCE_FINISH_REQUESTED = "force_finish_requested"


RuntimeEventType = EventType | str
EventTypeKey = NewType("EventTypeKey", str)


def event_type_value(event_type: RuntimeEventType) -> str:
    """Normalize runtime event type into canonical string value."""
    if isinstance(event_type, EventType):
        return event_type.value
    return str(event_type).strip()


def event_type_key(event_type: RuntimeEventType) -> EventTypeKey:
    """Normalize runtime event type into typed subscriber-table key."""
    return EventTypeKey(event_type_value(event_type))


class WakeupReason(str, Enum):
    """Enumerate wakeup reasons attached to wakeup event payload."""

    USER_MESSAGE = "user_message"
    SUBTASK_COMPLETED = "subtask_completed"
    SYSTEM = "system"


@dataclass
class RuntimeEvent:
    """Represent one publishable runtime event."""

    type: RuntimeEventType
    session_id: str
    payload: JsonObject = field(default_factory=dict)
    source: str = "runtime"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def type_value(self) -> str:
        """Expose normalized event type string."""
        return event_type_value(self.type)

    def to_dict(self) -> JsonObject:
        """Serialize event to dictionary payload.

        Returns:
            dict[str, Any]: JSON-compatible event mapping.
        """
        return {
            "id": self.id,
            "type": self.type_value,
            "session_id": self.session_id,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "RuntimeEvent":
        """Construct RuntimeEvent from dictionary payload.

        Args:
            data (dict[str, Any]): Serialized event mapping.

        Returns:
            RuntimeEvent: Reconstructed event object.
        """
        raw_type = str(data.get("type") or "").strip()
        try:
            event_type: RuntimeEventType = EventType(raw_type)
        except ValueError:
            event_type = raw_type
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            type=event_type,
            session_id=str(data.get("session_id") or ""),
            payload=data.get("payload") or {},
            source=str(data.get("source") or "runtime"),
            timestamp=int(data.get("timestamp") or int(time.time() * 1000)),
        )

    def to_json(self) -> str:
        """Serialize event into JSON text.

        Returns:
            str: JSON string representation.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "RuntimeEvent":
        """Construct RuntimeEvent from JSON text.

        Args:
            raw (str): Serialized event JSON.

        Returns:
            RuntimeEvent: Reconstructed event instance.
        """
        return cls.from_dict(json.loads(raw))


@dataclass
class RuntimeEventEnvelope:
    """Represent mailbox-stored event envelope format."""

    event_id: str
    event_type: RuntimeEventType
    payload: JsonObject
    source: str
    timestamp: int
    trigger_turn: bool = False

    @property
    def type_value(self) -> str:
        """Expose normalized envelope event type string."""
        return event_type_value(self.event_type)

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "RuntimeEventEnvelope":
        """Build mailbox envelope from runtime event object.

        Args:
            event (RuntimeEvent): Runtime event.

        Returns:
            RuntimeEventEnvelope: Envelope object for mailbox storage.
        """
        trigger_turn = normalize_trigger_turn(event.type_value, event.payload or {})
        return cls(
            event_id=event.id,
            event_type=event.type,
            payload=event.payload or {},
            source=event.source,
            timestamp=event.timestamp,
            trigger_turn=trigger_turn,
        )

    @property
    def type(self) -> RuntimeEventType:
        """Expose event_type through compatibility alias.

        Returns:
            RuntimeEventType: Envelope event type.
        """
        return self.event_type

    def to_dict(self) -> JsonObject:
        """Serialize envelope to dictionary payload.

        Returns:
            dict[str, Any]: JSON-compatible envelope mapping.
        """
        return {
            "event_id": self.event_id,
            "event_type": self.type_value,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
            "trigger_turn": self.trigger_turn,
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "RuntimeEventEnvelope":
        """Construct envelope from dictionary payload.

        Args:
            data (dict[str, Any]): Serialized envelope mapping.

        Returns:
            RuntimeEventEnvelope: Reconstructed envelope object.
        """
        raw_event_type = str(data.get("event_type") or "").strip()
        try:
            event_type: RuntimeEventType = EventType(raw_event_type)
        except ValueError:
            event_type = raw_event_type
        trigger_turn = normalize_trigger_turn(event_type_value(event_type), data)
        return cls(
            event_id=str(data.get("event_id") or ""),
            event_type=event_type,
            payload=data.get("payload") or {},
            source=str(data.get("source") or "runtime"),
            timestamp=int(data.get("timestamp") or int(time.time() * 1000)),
            trigger_turn=trigger_turn,
        )

    def to_json(self) -> str:
        """Serialize envelope to JSON text.

        Returns:
            str: JSON string representation.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "RuntimeEventEnvelope":
        """Construct envelope from JSON text.

        Args:
            raw (str): Serialized envelope JSON.

        Returns:
            RuntimeEventEnvelope: Reconstructed envelope.
        """
        return cls.from_dict(json.loads(raw))


@dataclass
class SessionRuntimeState:
    """Session runtime state.

    Includes inflight/wakeup flags, heartbeat timestamp, and event mailbox.
    """

    session_id: str
    inflight: bool = False
    wakeup_queued: bool = False
    heartbeat_at: float = field(default_factory=time.time)
    mailbox: list[RuntimeEventEnvelope] = field(default_factory=list)

    def touch(self, now: float | None = None) -> None:
        """Update heartbeat timestamp.

        Args:
            now (float | None): Explicit timestamp override.

        Returns:
            None
        """
        self.heartbeat_at = now if now is not None else time.time()

    def enqueue_event(self, event: RuntimeEvent) -> None:
        """Append event to mailbox and refresh heartbeat.

        Args:
            event (RuntimeEvent): Incoming runtime event.

        Returns:
            None
        """
        self.mailbox.append(RuntimeEventEnvelope.from_runtime_event(event))
        self.touch()

    def consume_mailbox(self) -> list[RuntimeEventEnvelope]:
        """Consume all pending mailbox events atomically.

        Returns:
            list[RuntimeEventEnvelope]: Previously queued events.
        """
        consumed = list(self.mailbox)
        self.mailbox.clear()
        return consumed

    def has_mailbox_events(self) -> bool:
        """Check whether mailbox currently has events.

        Returns:
            bool: True when mailbox is non-empty.
        """
        return len(self.mailbox) > 0

    def has_triggerable_mailbox_events(self) -> bool:
        """Check whether mailbox has events eligible to trigger planner turn.

        Returns:
            bool: True when at least one mailbox event requests turn processing.
        """
        return any(item.trigger_turn for item in self.mailbox)

    def to_dict(self) -> JsonObject:
        """Serialize runtime state to dictionary payload.

        Returns:
            dict[str, Any]: JSON-compatible state mapping.
        """
        return {
            "session_id": self.session_id,
            "inflight": self.inflight,
            "wakeup_queued": self.wakeup_queued,
            "heartbeat_at": self.heartbeat_at,
            "mailbox": [entry.to_dict() for entry in self.mailbox],
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "SessionRuntimeState":
        """Construct SessionRuntimeState from dictionary payload.

        Args:
            data (dict[str, Any]): Serialized runtime state mapping.

        Returns:
            SessionRuntimeState: Reconstructed state object.
        """
        mailbox_raw = data.get("mailbox") or []
        mailbox = [RuntimeEventEnvelope.from_dict(item) for item in mailbox_raw if isinstance(item, dict)]
        return cls(
            session_id=str(data.get("session_id") or ""),
            inflight=bool(data.get("inflight", False)),
            wakeup_queued=bool(data.get("wakeup_queued", False)),
            heartbeat_at=float(data.get("heartbeat_at") or time.time()),
            mailbox=mailbox,
        )

    def to_json(self) -> str:
        """Serialize runtime state to JSON text.

        Returns:
            str: JSON representation.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "SessionRuntimeState":
        """Construct runtime state from JSON text.

        Args:
            raw (str): Serialized runtime state JSON.

        Returns:
            SessionRuntimeState: Reconstructed runtime state.
        """
        return cls.from_dict(json.loads(raw))


EventHandler = Callable[[RuntimeEvent], Awaitable[None]]


class SessionEventBus:
    """Event bus with per-session serialization and cross-session concurrency."""

    def __init__(self, worker_count: int = 1) -> None:
        """Initialize event bus workers and internal scheduling structures.

        Args:
            worker_count (int): Number of concurrent worker tasks.

        Returns:
            None
        """
        self.worker_count = max(1, worker_count)
        self._session_queues: dict[str, asyncio.Queue[RuntimeEvent]] = {}
        self._ready_sessions: asyncio.Queue[str] = asyncio.Queue()
        self._scheduled_sessions: set[str] = set()
        self._subscribers: dict[EventTypeKey, list[EventHandler]] = defaultdict(list)
        self._workers: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._running = False

    def subscribe(self, event_type: RuntimeEventType, callback: EventHandler) -> None:
        """Register subscriber callback for event type.

        Args:
            event_type (RuntimeEventType): Event type to subscribe.
            callback (EventHandler): Async handler callable.

        Returns:
            None
        """
        self._subscribers[event_type_key(event_type)].append(callback)

    async def publish(self, event: RuntimeEvent) -> None:
        """Publish event into session queue and schedule dispatch.

        Args:
            event (RuntimeEvent): Event instance to publish.

        Returns:
            None
        """
        async with self._lock:
            queue = self._session_queues.get(event.session_id)
            if queue is None:
                queue = asyncio.Queue()
                self._session_queues[event.session_id] = queue
            await queue.put(event)
            if event.session_id not in self._scheduled_sessions:
                self._scheduled_sessions.add(event.session_id)
                await self._ready_sessions.put(event.session_id)

    async def _pop_next_event(self, session_id: str) -> RuntimeEvent | None:
        """Pop next queued event for given session.

        Args:
            session_id (str): Session identifier.

        Returns:
            RuntimeEvent | None: Next event or None when queue missing/empty.
        """
        async with self._lock:
            queue = self._session_queues.get(session_id)
            if queue is None:
                self._scheduled_sessions.discard(session_id)
                return None
            if queue.empty():
                self._scheduled_sessions.discard(session_id)
                self._session_queues.pop(session_id, None)
                return None
            return queue.get_nowait()

    async def _reschedule_session(self, session_id: str) -> None:
        """Reschedule session when queue still has pending events.

        Args:
            session_id (str): Session identifier.

        Returns:
            None
        """
        async with self._lock:
            queue = self._session_queues.get(session_id)
            if queue is None or queue.empty():
                self._scheduled_sessions.discard(session_id)
                self._session_queues.pop(session_id, None)
                return
            await self._ready_sessions.put(session_id)

    async def _dispatch(self, event: RuntimeEvent, worker_index: int) -> None:
        """Dispatch event to all subscribers with unified error logging.

        Args:
            event (RuntimeEvent): Event to dispatch.
            worker_index (int): Worker index for logging context.

        Returns:
            None
        """
        type_key = event_type_key(event.type)
        handlers = self._subscribers.get(type_key, [])
        if not handlers:
            logger.warning(f"No subscriber for event type={event.type_value} session={event.session_id}")
            return
        results = await asyncio.gather(*(handler(event) for handler in handlers), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    f"Event handler failed for type={event.type_value} session={event.session_id} worker={worker_index}: {result}",
                    exc_info=True,
                )

    async def _worker_loop(self, worker_index: int) -> None:
        """Run one worker dispatch loop.

        Args:
            worker_index (int): Worker index for logging.

        Returns:
            None
        """
        while self._running:
            try:
                session_id = await self._ready_sessions.get()
                event = await self._pop_next_event(session_id)
                if event is None:
                    self._ready_sessions.task_done()
                    continue
                await self._dispatch(event, worker_index)
                await self._reschedule_session(session_id)
                self._ready_sessions.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Event bus worker {worker_index} failed: {exc}", exc_info=True)

    async def start(self) -> None:
        """Start worker tasks for event dispatch loop.

        Returns:
            None
        """
        if self._running:
            return
        self._running = True
        for index in range(self.worker_count):
            task = asyncio.create_task(self._worker_loop(index))
            self._workers.append(task)

    async def stop(self) -> None:
        """Stop dispatch loop and reclaim worker tasks.

        Returns:
            None
        """
        self._running = False
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

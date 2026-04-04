# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Typed runtime event payload contracts and parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fairyclaw.core.agent.constants import TaskType
from fairyclaw.core.agent.hooks.protocol import JsonObject

WAKEUP_SYSTEM_REASON = "system"

if TYPE_CHECKING:
    from fairyclaw.core.events.bus import RuntimeEvent


def _runtime_event_type_value(event: "RuntimeEvent") -> str:
    raw_type = getattr(event, "type", "")
    value = getattr(raw_type, "value", raw_type)
    return str(value or "").strip()


@dataclass(frozen=True)
class EventPayloadBase:
    """Shared runtime event metadata exposed to event hooks."""

    session_id: str
    event_id: str
    source: str
    timestamp_ms: int


@dataclass(frozen=True)
class UserMessageReceivedEventPayload(EventPayloadBase):
    """Payload contract for user message received events."""

    trigger_turn: bool = True
    task_type: str = TaskType.GENERAL.value
    enabled_groups: list[str] | None = None

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "UserMessageReceivedEventPayload":
        payload = event.payload or {}
        task_type = payload.get("task_type")
        raw_groups = payload.get("enabled_groups")
        if not isinstance(raw_groups, list):
            raw_groups = payload.get("selected_groups")
        enabled_groups = [g for g in raw_groups if isinstance(g, str) and g.strip()] if isinstance(raw_groups, list) else None
        trigger_turn_raw = payload.get("trigger_turn")
        return cls(
            session_id=event.session_id,
            event_id=event.id,
            source=event.source,
            timestamp_ms=event.timestamp,
            trigger_turn=trigger_turn_raw if isinstance(trigger_turn_raw, bool) else True,
            task_type=task_type.strip() if isinstance(task_type, str) and task_type.strip() else TaskType.GENERAL.value,
            enabled_groups=enabled_groups if enabled_groups else None,
        )


@dataclass(frozen=True)
class WakeupRequestedEventPayload(EventPayloadBase):
    """Payload contract for wakeup requested events."""

    reason: str = WAKEUP_SYSTEM_REASON

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "WakeupRequestedEventPayload":
        payload = event.payload or {}
        reason = payload.get("reason")
        return cls(
            session_id=event.session_id,
            event_id=event.id,
            source=event.source,
            timestamp_ms=event.timestamp,
            reason=reason if isinstance(reason, str) and reason.strip() else WAKEUP_SYSTEM_REASON,
        )


@dataclass(frozen=True)
class SubtaskCompletedEventPayload(EventPayloadBase):
    """Payload contract for subtask completed events."""

    trigger_turn: bool = True
    sub_session_id: str | None = None
    aggregated: JsonObject | None = None
    status: str | None = None
    immediate: bool = False

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "SubtaskCompletedEventPayload":
        payload = event.payload or {}
        trigger_turn_raw = payload.get("trigger_turn")
        sub_session_id = payload.get("sub_session_id")
        aggregated = payload.get("aggregated")
        status = payload.get("status")
        immediate_raw = payload.get("immediate")
        return cls(
            session_id=event.session_id,
            event_id=event.id,
            source=event.source,
            timestamp_ms=event.timestamp,
            trigger_turn=trigger_turn_raw if isinstance(trigger_turn_raw, bool) else True,
            sub_session_id=sub_session_id if isinstance(sub_session_id, str) and sub_session_id.strip() else None,
            aggregated=aggregated if isinstance(aggregated, dict) else None,
            status=status if isinstance(status, str) and status.strip() else None,
            immediate=bool(immediate_raw) if isinstance(immediate_raw, bool) else False,
        )


@dataclass(frozen=True)
class FileUploadReceivedEventPayload(EventPayloadBase):
    """Payload contract for file-upload received runtime events."""

    file_id: str = ""
    filename: str = ""
    mime_type: str = "application/octet-stream"

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "FileUploadReceivedEventPayload":
        payload = event.payload or {}
        file_id = payload.get("file_id")
        filename = payload.get("filename")
        mime_type = payload.get("mime_type")
        return cls(
            session_id=event.session_id,
            event_id=event.id,
            source=event.source,
            timestamp_ms=event.timestamp,
            file_id=file_id if isinstance(file_id, str) else "",
            filename=filename if isinstance(filename, str) else "",
            mime_type=mime_type if isinstance(mime_type, str) and mime_type.strip() else "application/octet-stream",
        )


@dataclass(frozen=True)
class GenericRuntimeEventPayload(EventPayloadBase):
    """Generic typed payload for manifest-declared custom runtime events."""

    event_type: str
    data: JsonObject
    schema_definition: JsonObject | None = None

    @classmethod
    def from_runtime_event(cls, event: "RuntimeEvent") -> "GenericRuntimeEventPayload":
        payload = event.payload or {}
        return cls(
            session_id=event.session_id,
            event_id=event.id,
            source=event.source,
            timestamp_ms=event.timestamp,
            event_type=_runtime_event_type_value(event),
            data=payload if isinstance(payload, dict) else {},
        )


@dataclass(frozen=True)
class ForceFinishRequestedEventPayload(EventPayloadBase):
    """Payload contract for force-finish runtime events."""

    trigger_turn: bool = False
    reason: str | None = None
    stage: str | None = None
    turn_id: str | None = None
    task_type: str | None = None
    enabled_groups: list[str] | None = None
    is_sub_session: bool = False
    details: JsonObject | None = None

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "ForceFinishRequestedEventPayload":
        payload = event.payload or {}
        trigger_turn_raw = payload.get("trigger_turn")
        reason = payload.get("reason")
        stage = payload.get("stage")
        turn_id = payload.get("turn_id")
        task_type = payload.get("task_type")
        enabled_groups_raw = payload.get("enabled_groups")
        details = payload.get("details")
        is_sub_session_raw = payload.get("is_sub_session")
        return cls(
            session_id=event.session_id,
            event_id=event.id,
            source=event.source,
            timestamp_ms=event.timestamp,
            trigger_turn=trigger_turn_raw if isinstance(trigger_turn_raw, bool) else False,
            reason=reason if isinstance(reason, str) and reason.strip() else None,
            stage=stage if isinstance(stage, str) and stage.strip() else None,
            turn_id=turn_id if isinstance(turn_id, str) and turn_id.strip() else None,
            task_type=task_type if isinstance(task_type, str) and task_type.strip() else None,
            enabled_groups=(
                [group for group in enabled_groups_raw if isinstance(group, str) and group.strip()]
                if isinstance(enabled_groups_raw, list)
                else None
            ),
            is_sub_session=bool(is_sub_session_raw) if isinstance(is_sub_session_raw, bool) else False,
            details=details if isinstance(details, dict) else None,
        )


RuntimeEventPayload = (
    UserMessageReceivedEventPayload
    | SubtaskCompletedEventPayload
    | WakeupRequestedEventPayload
    | FileUploadReceivedEventPayload
    | ForceFinishRequestedEventPayload
    | GenericRuntimeEventPayload
)


def payload_from_runtime_event(event: RuntimeEvent) -> RuntimeEventPayload:
    """Convert RuntimeEvent into concrete typed payload object."""
    event_type = _runtime_event_type_value(event)
    if event_type == "user_message_received":
        return UserMessageReceivedEventPayload.from_runtime_event(event)
    if event_type == "subtask_completed":
        return SubtaskCompletedEventPayload.from_runtime_event(event)
    if event_type == "wakeup_requested":
        return WakeupRequestedEventPayload.from_runtime_event(event)
    if event_type == "file_upload_received":
        return FileUploadReceivedEventPayload.from_runtime_event(event)
    if event_type == "force_finish_requested":
        return ForceFinishRequestedEventPayload.from_runtime_event(event)
    return GenericRuntimeEventPayload.from_runtime_event(event)


def normalize_trigger_turn(event_type_value: str, payload: JsonObject) -> bool:
    """Resolve trigger_turn with event-type defaults."""
    trigger_turn_raw = payload.get("trigger_turn")
    if isinstance(trigger_turn_raw, bool):
        return trigger_turn_raw
    return event_type_value in {"user_message_received", "subtask_completed"}

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime control envelope types (gateway ↔ business ↔ web).

These payloads are channel-agnostic: the same JSON may appear in ``GatewayOutboundMessage``
``kind=event`` content or Web gateway WebSocket messages.

Sub-agent streaming reuses the same push ``kind`` values as the main session; clients tell
main vs child apart using ``body.session_id``. See ``GATEWAY_RUNTIME_PROTOCOL.md``
(Sub-session push routing).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# event_type values inside GatewayOutboundMessage.content when kind == "event"
EVENT_TYPE_TELEMETRY = "telemetry"
EVENT_TYPE_SUBAGENT_TASKS = "subagent_tasks"
EVENT_TYPE_SESSION_SUMMARIES = "session_summaries"
EVENT_TYPE_CONFIG_SNAPSHOT = "config_snapshot"
EVENT_TYPE_TOOL_CALL = "tool_call"
EVENT_TYPE_TOOL_RESULT = "tool_result"
EVENT_TYPE_TIMER_TICK = "timer_tick"


def _json_safe(value: Any) -> Any:
    """Recursively normalize values for JSON (fallback: str)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def parse_tool_arguments_json(arguments_json: str) -> dict[str, Any]:
    """Parse LLM tool ``arguments_json`` into a dict for ``ToolCallEnvelope``."""
    raw = (arguments_json or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"_value": _json_safe(parsed)}


@dataclass(frozen=True)
class HeartbeatInfo:
    """Heartbeat line for TelemetrySnapshot."""

    status: str
    server_time_ms: int
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Lightweight telemetry signal for UI heartbeat/status."""

    heartbeat: HeartbeatInfo
    reins_enabled: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["heartbeat"] = self.heartbeat.to_dict()
        return _drop_none(d)


@dataclass(frozen=True)
class SubagentTaskState:
    """One background sub-agent task row."""

    task_id: str
    parent_session_id: str
    label: str
    status: str
    updated_at_ms: int
    status_display: str | None = None
    task_type: str | None = None
    instruction: str | None = None
    child_session_id: str | None = None
    detail: str | None = None
    event_count: int | None = None
    last_event_at_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass(frozen=True)
class MessagePreviewLine:
    """One line in session preview."""

    role: str
    text: str
    ts_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


@dataclass(frozen=True)
class SessionSummary:
    """Session list row with message preview."""

    session_id: str
    title: str | None
    updated_at_ms: int | None
    preview_messages: tuple[MessagePreviewLine, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "updated_at_ms": self.updated_at_ms,
            "preview_messages": [p.to_dict() for p in self.preview_messages],
        }


# Keys allowed in SystemEnvironmentSlice (fairyclaw.env lines 1–24 FAIRYCLAW_* only).
SYSTEM_ENV_WHITELIST: frozenset[str] = frozenset(
    {
        "FAIRYCLAW_API_TOKEN",
        "FAIRYCLAW_DATABASE_URL",
        "FAIRYCLAW_DATA_DIR",
        "FAIRYCLAW_HOST",
        "FAIRYCLAW_PORT",
        "FAIRYCLAW_LLM_ENDPOINTS_CONFIG_PATH",
        "FAIRYCLAW_FILESYSTEM_ROOT_DIR",
        "FAIRYCLAW_LOG_LEVEL",
        "FAIRYCLAW_LOG_FILE_PATH",
        "FAIRYCLAW_LOG_TO_STDOUT",
        "FAIRYCLAW_CAPABILITIES_DIR",
        "FAIRYCLAW_EVENT_BUS_WORKER_COUNT",
        "FAIRYCLAW_PLANNER_HEARTBEAT_SECONDS",
        "FAIRYCLAW_PLANNER_WAKEUP_DEBOUNCE_MS",
        "FAIRYCLAW_ROUTER_PROFILE_NAME",
        "FAIRYCLAW_HOOK_DEFAULT_TIMEOUT_MS",
        "FAIRYCLAW_ENABLE_HOOK_RUNTIME",
        "FAIRYCLAW_ENABLE_RAG_PIPELINE",
        "FAIRYCLAW_REINS_ENABLED",
        "FAIRYCLAW_REINS_BUDGET_DAILY_USD",
        "FAIRYCLAW_REINS_ON_EXCEED",
    }
)


def validate_system_env_slice(data: dict[str, Any]) -> dict[str, str]:
    """Return only whitelisted keys with string values."""
    out: dict[str, str] = {}
    for key, value in data.items():
        if key not in SYSTEM_ENV_WHITELIST:
            continue
        if value is None:
            continue
        out[key] = str(value)
    return out


@dataclass(frozen=True)
class CapabilityGroupPolicy:
    """Skill group visibility for main planner vs sub-agent."""

    name: str
    description: str
    always_enable_planner: bool
    always_enable_subagent: bool
    manifest_version: str = "1.0"
    routing_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


@dataclass(frozen=True)
class ToolCallEnvelope:
    """Emitted before a tool executes (name + arguments; correlates via ``tool_call_id``)."""

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_content_dict(self) -> dict[str, Any]:
        """Fields for ``GatewayOutboundMessage.event`` ``content`` (``event_type`` set by caller)."""
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": _json_safe(self.arguments),
        }


@dataclass(frozen=True)
class ToolResultEnvelope:
    """Emitted after a tool finishes (success or failure; correlates via ``tool_call_id``)."""

    tool_call_id: str
    tool_name: str
    ok: bool
    result: Any | None = None
    error_message: str | None = None
    duration_ms: int | None = None

    def to_content_dict(self) -> dict[str, Any]:
        """Fields for ``GatewayOutboundMessage.event`` ``content`` (``event_type`` set by caller)."""
        body: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "ok": self.ok,
            "result": _json_safe(self.result) if self.result is not None else None,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
        }
        return _drop_none(body)


@dataclass(frozen=True)
class TimerTickEnvelope:
    """Emitted when timer watchdog delivers one timer tick to a session."""

    job_id: str
    mode: str
    owner_session_id: str
    creator_session_id: str
    run_index: int
    payload: str | None = None
    next_fire_at_ms: int | None = None

    def to_content_dict(self) -> dict[str, Any]:
        return _drop_none(asdict(self))

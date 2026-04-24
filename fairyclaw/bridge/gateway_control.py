# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Business-side handlers for Bridge ``gateway_control`` frames (LLM YAML, system env, capability policies)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fairyclaw.config import settings
from fairyclaw.core.domain import EventType
from fairyclaw.config.loader import merge_whitelisted_env, read_env_file
from fairyclaw.config.locations import resolve_fairyclaw_env_path
from fairyclaw.core.gateway_protocol.control_envelope import (
    SYSTEM_ENV_WHITELIST,
    CapabilityGroupPolicy,
    validate_system_env_slice,
)
from fairyclaw.infrastructure.database.models import EventModel
from fairyclaw.infrastructure.database.repository import EventRepository, SessionRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.infrastructure.llm.config import apply_llm_document, get_llm_document
from fairyclaw.core.events.runtime import get_user_gateway

logger = logging.getLogger(__name__)

_HISTORY_LIMIT_CAP = 500


def _segments_preview(content: Any) -> str:
    """Flatten persisted segment list into display text for the web UI."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        st = str(item.get("type") or "")
        if st == "text":
            parts.append(str(item.get("text") or item.get("content") or ""))
        elif st == "file":
            fid = item.get("file_id")
            parts.append(f"[file:{fid}]" if fid else "[file]")
        elif st == "image_url":
            img = item.get("image_url")
            url = img.get("url") if isinstance(img, dict) else None
            parts.append(f"[image:{url}]" if url else "[image]")
        elif st == "code_block":
            parts.append(str(item.get("text") or item.get("content") or ""))
        else:
            # Future / foreign segment shapes: pull any inline string.
            v = item.get("text")
            if isinstance(v, str) and v.strip():
                parts.append(v)
                continue
            v = item.get("content")
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return "\n".join(parts).strip()


def _history_row(ev: EventModel) -> dict[str, Any]:
    ts_ms = int(ev.timestamp.timestamp() * 1000)
    if ev.type == EventType.SESSION_EVENT.value:
        role_raw = (ev.role or "assistant").strip().lower()
        role = role_raw if role_raw in {"user", "assistant", "system"} else "assistant"
        return {
            "kind": "session_event",
            "role": role,
            "text": _segments_preview(ev.content),
            "ts_ms": ts_ms,
        }
    if ev.type == EventType.OPERATION_EVENT.value:
        tr = ev.tool_result
        result_preview: str | None
        if tr is None:
            result_preview = None
        elif isinstance(tr, str):
            result_preview = tr[:2000] + ("…" if len(tr) > 2000 else "")
        else:
            try:
                raw = json.dumps(tr, ensure_ascii=False)
            except TypeError:
                raw = str(tr)
            result_preview = raw[:2000] + ("…" if len(raw) > 2000 else "")
        return {
            "kind": "operation_event",
            "tool_name": ev.tool_name or "",
            "ts_ms": ts_ms,
            "result_preview": result_preview,
        }
    return {"kind": "unknown", "ts_ms": ts_ms}


def _coerce_str_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _apply_env_to_settings(updates: dict[str, str]) -> None:
    """Best-effort in-process settings refresh for whitelisted FAIRYCLAW_* keys."""
    from fairyclaw.config.settings import Settings

    for key, val in updates.items():
        if not key.startswith("FAIRYCLAW_"):
            continue
        attr = key.removeprefix("FAIRYCLAW_").lower()
        if attr not in Settings.model_fields:
            continue
        try:
            coerced = _coerce_str_value(val)
            setattr(settings, attr, coerced)
        except Exception as exc:
            logger.warning("Could not apply %s=%r to settings: %s", key, val, exc)


class BusinessGatewayControl:
    """Dispatch ``gateway_control`` / ``gateway_control_ack`` payloads on the Business process (Bridge WebSocket only)."""

    def __init__(self, planner: Any) -> None:
        self._planner = planner

    async def handle(self, op: str, body: dict[str, Any]) -> dict[str, Any]:
        if op == "config.llm.get":
            return {"document": get_llm_document()}
        if op == "config.llm.put":
            doc = body.get("document")
            if not isinstance(doc, dict):
                raise ValueError("body.document must be an object")
            apply_llm_document(doc)
            self._planner.reload_llm_client()
            return {"ok": True}
        if op == "config.system_env.get":
            env = read_env_file(resolve_fairyclaw_env_path())
            filtered = {k: v for k, v in env.items() if k in SYSTEM_ENV_WHITELIST}
            return {"env": filtered}
        if op == "config.system_env.put":
            raw = body.get("env")
            if not isinstance(raw, dict):
                raise ValueError("body.env must be an object")
            str_map = {str(k): str(v) for k, v in raw.items() if v is not None}
            if "FAIRYCLAW_API_TOKEN" in str_map:
                raise ValueError("FAIRYCLAW_API_TOKEN cannot be changed via control plane")
            validated = validate_system_env_slice(str_map)
            merge_whitelisted_env(resolve_fairyclaw_env_path(), validated, whitelist=SYSTEM_ENV_WHITELIST)
            _apply_env_to_settings(validated)
            return {"ok": True}
        if op == "capabilities.list":
            out: list[dict[str, Any]] = []
            for name, group in self._planner.registry.groups.items():
                pol = CapabilityGroupPolicy(
                    name=name,
                    description=group.description,
                    always_enable_planner=group.always_enable_planner,
                    always_enable_subagent=group.always_enable_subagent,
                    manifest_version=group.manifest_version,
                    routing_hint=group.routing_hint,
                )
                out.append(pol.to_dict())
            return {"groups": out}
        if op == "capabilities.put":
            group_name = str(body.get("group_name") or "").strip()
            if not group_name:
                raise ValueError("group_name required")
            patch = body.get("patch")
            if not isinstance(patch, dict):
                raise ValueError("patch must be an object")
            self._planner.registry.apply_group_policy_and_persist(group_name, patch)
            return {"ok": True}
        if op == "sessions.list":
            async with AsyncSessionLocal() as db:
                repo = SessionRepository(db)
                rows = await repo.list_all()
            sessions = [
                {
                    "session_id": r.session_id,
                    "title": r.title,
                    "created_at": r.created_at,
                    "last_activity_at": r.last_activity_at,
                    "event_count": r.event_count,
                }
                for r in rows
            ]
            return {"sessions": sessions}
        if op == "sessions.history":
            sid = str(body.get("session_id") or "").strip()
            if not sid:
                raise ValueError("session_id required")
            raw_limit = body.get("limit")
            if isinstance(raw_limit, int) and raw_limit > 0:
                limit = min(raw_limit, _HISTORY_LIMIT_CAP)
            else:
                limit = 200
            async with AsyncSessionLocal() as db:
                sess_repo = SessionRepository(db)
                model = await sess_repo.get(sid)
                if model is None:
                    raise ValueError(f"Session not found: {sid}")
                event_repo = EventRepository(db)
                events = await event_repo.history(session_id=sid, limit=limit)
            return {
                "session_id": sid,
                "events": [_history_row(e) for e in events],
            }
        if op == "sessions.subagent_tasks":
            sid = str(body.get("session_id") or "").strip()
            if not sid:
                raise ValueError("session_id required")
            gw = get_user_gateway()
            if gw is None:
                return {"session_id": sid, "tasks": []}
            tasks = await gw.collect_subagent_task_rows(sid)
            return {"session_id": sid, "tasks": tasks}
        raise ValueError(f"Unknown gateway_control op: {op}")

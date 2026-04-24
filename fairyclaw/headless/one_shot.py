# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Single-process agent run: bootstrap runtime, one user turn, optional idle wait (ClawBench)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fairyclaw.session_history_utils import last_assistant_reply_from_history_events
from fairyclaw.bridge.gateway_control import BusinessGatewayControl
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.gateway_protocol.ingress import GatewayIngressService
from fairyclaw.core.gateway_protocol.models import GatewayInboundMessage, new_frame_id
from fairyclaw.infrastructure.database.repository import GatewaySessionRouteRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.runtime.lifecycle import BusinessRuntime, shutdown_business_runtime, startup_business_runtime

# Same as `WebGatewayAdapter.adapter_key` (web/CLI user channel).
ADAPTER_KEY_HTTP = "http"

logger = logging.getLogger(__name__)


async def _wait_scheduler_done(
    scheduler,
    *,
    timeout_sec: float,
    min_wait_sec: float,
    poll_sec: float,
) -> bool:
    """Wait until the scheduler has no active sessions (all turns + sub-agents complete)."""
    deadline = time.perf_counter() + timeout_sec
    # Allow time for the message to propagate from bus → scheduler → session_states
    await asyncio.sleep(min_wait_sec)
    while time.perf_counter() < deadline:
        async with scheduler.state_lock:
            if not scheduler.session_states:
                return True
        await asyncio.sleep(poll_sec)
    return False


async def _last_assistant_text(
    control: BusinessGatewayControl,
    session_id: str,
) -> str | None:
    """Last assistant-visible text from session history (same as ``sessions.history`` / ``get``)."""
    try:
        body = await control.handle("sessions.history", {"session_id": session_id, "limit": 500})
    except Exception:
        return None
    return last_assistant_reply_from_history_events(
        body.get("events") if isinstance(body.get("events"), list) else None
    )


def _load_session_map(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, UnicodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _save_session_map(path: Path, mapping: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(sorted(mapping.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def _resolve_session_id(
    map_path: Path,
    session_name: str,
) -> str:
    m = _load_session_map(map_path)
    if session_name in m:
        return m[session_name]
    ingress = GatewayIngressService()
    sid = await ingress.open_session(
        platform="web",
        title=session_name,
        meta={"source": "fairyclaw_agent", "cli": "one_shot"},
    )
    async with AsyncSessionLocal() as db:
        await GatewaySessionRouteRepository(db).bind(
            session_id=sid,
            adapter_key=ADAPTER_KEY_HTTP,
            sender_ref=None,
        )
    m[session_name] = sid
    _save_session_map(map_path, m)
    return sid


async def run_in_process_agent(
    *,
    map_path: Path,
    session_name: str,
    text: str,
    wait_idle: bool,
    timeout_sec: float,
    poll_sec: float = 0.5,
    min_wait_sec: float = 2.0,
) -> dict[str, Any]:
    """Start Business runtime, create/reuse session, send one user message, optionally wait for idle history."""
    rt = await startup_business_runtime()
    try:
        return await _with_runtime(
            rt,
            map_path=map_path,
            session_name=session_name,
            text=text,
            wait_idle=wait_idle,
            timeout_sec=timeout_sec,
            poll_sec=poll_sec,
            min_wait_sec=min_wait_sec,
        )
    finally:
        await shutdown_business_runtime(rt)


async def _with_runtime(
    rt: BusinessRuntime,
    *,
    map_path: Path,
    session_name: str,
    text: str,
    wait_idle: bool,
    timeout_sec: float,
    poll_sec: float,
    min_wait_sec: float,
) -> dict[str, Any]:
    sid = await _resolve_session_id(map_path, session_name)
    control = BusinessGatewayControl(rt.planner)
    ingress = GatewayIngressService()
    segments = (ContentSegment.text_segment(text),)
    has_text = bool(text.strip())
    await ingress.submit_message(
        GatewayInboundMessage(
            session_id=sid,
            adapter_key=ADAPTER_KEY_HTTP,
            segments=segments,
            trigger_turn=bool(segments and has_text),
            meta={"message_id": new_frame_id("headless")},
        ),
        bus=rt.bus,
    )
    if not wait_idle:
        return {
            "ok": True,
            "session_id": sid,
            "reason": "sent",
            "mode": "no_wait",
        }
    t_send = time.perf_counter()
    done = await _wait_scheduler_done(
        rt.scheduler,
        timeout_sec=timeout_sec,
        min_wait_sec=min_wait_sec,
        poll_sec=poll_sec,
    )
    elapsed = time.perf_counter() - t_send
    reply = await _last_assistant_text(control, sid)
    result: dict[str, Any] = {
        "ok": done,
        "session_id": sid,
        "reason": "scheduler_idle" if done else "timeout",
        "elapsed_sec": round(elapsed, 3),
    }
    if reply:
        result["reply"] = reply
    return result

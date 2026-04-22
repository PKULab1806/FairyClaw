# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session scheduler handlers for runtime event bus."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fairyclaw.config.settings import settings
from fairyclaw.core.agent.constants import SUB_SESSION_MARKER
from fairyclaw.core.agent.planning.planner import Planner
from fairyclaw.core.agent.planning.turn_runner import process_background_turn
from fairyclaw.core.agent.types import SessionKind, TurnRequest, TurnRuntimePrefs
from fairyclaw.core.events.bus import (
    EventType,
    RuntimeEvent,
    SessionEventBus,
    SessionRuntimeState,
    WakeupReason,
    event_type_value,
)
from fairyclaw.core.events.payloads import (
    SubtaskCompletedEventPayload,
    UserMessageReceivedEventPayload,
    WakeupRequestedEventPayload,
)
from fairyclaw.core.events.plugin_dispatcher import EventPluginDispatcher
from fairyclaw.core.events.runtime import get_user_gateway
from fairyclaw.core.runtime.timer_runtime_store import get_timer_runtime_store
from fairyclaw.infrastructure.database.models import SessionModel
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

DEFAULT_TASK_TYPE = "general"


class RuntimeSessionScheduler:
    """Own mailbox, debounce and wakeup scheduling for session events."""

    def __init__(
        self,
        bus: SessionEventBus,
        planner: Planner,
        event_dispatcher: EventPluginDispatcher,
    ) -> None:
        self.bus = bus
        self.planner = planner
        self.event_dispatcher = event_dispatcher
        self.session_states: dict[str, SessionRuntimeState] = {}
        self.state_lock = asyncio.Lock()
        self.debounce_tasks: dict[str, asyncio.Task[None]] = {}
        self.watchdog_task: asyncio.Task[None] | None = None
        self.timer_watchdog_task: asyncio.Task[None] | None = None
        self.timer_worker_id = f"timer_watchdog_{uuid.uuid4().hex[:8]}"

    async def start(self) -> None:
        subscribed: set[str] = set()
        for event_type in list(EventType) + sorted(self.planner.registry.get_declared_event_types()):
            normalized_type = event_type_value(event_type)
            if not normalized_type or normalized_type in subscribed:
                continue
            self.bus.subscribe(event_type, self.on_event)
            subscribed.add(normalized_type)
        await self.bus.start()
        self.watchdog_task = asyncio.create_task(self.heartbeat_watchdog())
        self.timer_watchdog_task = asyncio.create_task(self.timer_watchdog())

    async def stop(self) -> None:
        if self.watchdog_task:
            self.watchdog_task.cancel()
            await asyncio.gather(self.watchdog_task, return_exceptions=True)
            self.watchdog_task = None
        if self.timer_watchdog_task:
            self.timer_watchdog_task.cancel()
            await asyncio.gather(self.timer_watchdog_task, return_exceptions=True)
            self.timer_watchdog_task = None
        for task in self.debounce_tasks.values():
            task.cancel()
        if self.debounce_tasks:
            await asyncio.gather(*self.debounce_tasks.values(), return_exceptions=True)
        self.debounce_tasks.clear()

    def get_or_create_state(self, session_id: str) -> SessionRuntimeState:
        state = self.session_states.get(session_id)
        if state is None:
            state = SessionRuntimeState(session_id=session_id)
            self.session_states[session_id] = state
        return state

    def resolve_runtime_preferences(self, consumed_events: list[Any]) -> tuple[str, list[str] | None]:
        task_type = DEFAULT_TASK_TYPE
        enabled_groups: list[str] | None = None
        for event in reversed(consumed_events):
            if event_type_value(event.type) != EventType.USER_MESSAGE_RECEIVED.value:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            task_type_raw = payload.get("task_type")
            groups_raw = payload.get("enabled_groups")
            if not isinstance(groups_raw, list):
                groups_raw = payload.get("selected_groups")
            normalized_groups = [g for g in groups_raw if isinstance(g, str) and g.strip()] if isinstance(groups_raw, list) else None
            task_type = (
                task_type_raw.strip()
                if isinstance(task_type_raw, str) and task_type_raw.strip()
                else DEFAULT_TASK_TYPE
            )
            enabled_groups = normalized_groups if normalized_groups else None
            if task_type != DEFAULT_TASK_TYPE or enabled_groups is not None:
                break
        return task_type, enabled_groups

    async def resolve_sub_session_groups(
        self,
        session_id: str,
        event_groups: list[str] | None,
    ) -> list[str] | None:
        fallback_groups = event_groups or self.planner.registry.resolve_enabled_groups(is_sub_session=True)
        try:
            async with AsyncSessionLocal() as db:
                session_model = await db.get(SessionModel, session_id)
                if session_model is None:
                    return fallback_groups
                meta = dict(session_model.meta or {})
                persisted_raw = meta.get("enabled_groups")
                persisted_groups = (
                    [g for g in persisted_raw if isinstance(g, str) and g.strip()]
                    if isinstance(persisted_raw, list)
                    else []
                )
                if not bool(meta.get("routing_pending")):
                    return persisted_groups or fallback_groups

                route_input = str(meta.get("route_input") or "").strip()
                resolved_groups = persisted_groups or fallback_groups
                if route_input:
                    try:
                        routed = await self.planner.router.select_groups(route_input)
                        if isinstance(routed, list):
                            normalized = [g for g in routed if isinstance(g, str) and g.strip()]
                            if normalized:
                                resolved_groups = normalized
                    except Exception as exc:
                        logger.warning(
                            "Deferred sub-agent routing failed, using fallback groups. session=%s error=%s",
                            session_id,
                            exc,
                        )
                meta["enabled_groups"] = list(resolved_groups)
                meta["routing_pending"] = False
                meta.pop("route_input", None)
                session_model.meta = meta
                await db.commit()
                return resolved_groups
        except Exception as exc:
            logger.warning(
                "Failed to resolve deferred routing metadata for session=%s, use fallback groups. error=%s",
                session_id,
                exc,
            )
            return fallback_groups

    async def request_wakeup_if_needed(self, session_id: str, reason: str, source: str) -> None:
        should_publish = False
        async with self.state_lock:
            state = self.session_states.get(session_id)
            if state is None:
                return
            if not state.has_mailbox_events() or not state.has_triggerable_mailbox_events():
                return
            if state.inflight or state.wakeup_queued:
                return
            state.wakeup_queued = True
            should_publish = True
        if should_publish:
            await self.bus.publish(
                RuntimeEvent(
                    type=EventType.WAKEUP_REQUESTED,
                    session_id=session_id,
                    payload={"reason": reason},
                    source=source,
                )
            )

    async def run_user_message_debounce(self, session_id: str) -> None:
        wait_seconds = max(0.0, float(settings.planner_wakeup_debounce_ms) / 1000.0)
        try:
            await asyncio.sleep(wait_seconds)
            await self.request_wakeup_if_needed(
                session_id=session_id,
                reason=WakeupReason.USER_MESSAGE.value,
                source="debounce_timer",
            )
        finally:
            async with self.state_lock:
                current = self.debounce_tasks.get(session_id)
                if current is asyncio.current_task():
                    self.debounce_tasks.pop(session_id, None)

    async def beat(self, session_id: str, stop_signal: asyncio.Event) -> None:
        interval = max(1, settings.planner_heartbeat_seconds // 2)
        while not stop_signal.is_set():
            async with self.state_lock:
                self.get_or_create_state(session_id).touch()
            try:
                await asyncio.wait_for(stop_signal.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def run_session(self, session_id: str, wakeup_source: str, wakeup_reason: str) -> None:
        async with self.state_lock:
            state = self.session_states.get(session_id)
            if state is None:
                return
            state.wakeup_queued = False
            if state.inflight:
                return
            allow_empty_mailbox = wakeup_source not in {"event_bus", "heartbeat_watchdog", "debounce_timer", "subtask_completed"}
            if not state.has_mailbox_events() and not allow_empty_mailbox:
                return
            if state.has_mailbox_events() and not state.has_triggerable_mailbox_events():
                state.touch()
                return
            state.inflight = True
            consumed_events = state.consume_mailbox() if state.has_mailbox_events() else []
        task_type, enabled_groups = self.resolve_runtime_preferences(consumed_events)
        is_sub_session = SUB_SESSION_MARKER in session_id
        if is_sub_session:
            enabled_groups = await self.resolve_sub_session_groups(session_id, enabled_groups)
        try:
            session_role = "sub_session" if is_sub_session else "main_session"
            logger.info(
                "Planner wakeup: session=%s, session_role=%s, source=%s, reason=%s, batched_events=%s",
                session_id,
                session_role,
                wakeup_source,
                wakeup_reason,
                len(consumed_events),
            )
            stop_signal = asyncio.Event()
            heartbeat_task = asyncio.create_task(self.beat(session_id, stop_signal))
            try:
                await process_background_turn(
                    TurnRequest(
                        session_id=session_id,
                        user_segments=(),
                        runtime=TurnRuntimePrefs(task_type=task_type, enabled_groups=enabled_groups),
                        session_kind=SessionKind.SUB if is_sub_session else SessionKind.MAIN,
                    ),
                    self.planner,
                )
            finally:
                stop_signal.set()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
        finally:
            should_requeue = False
            async with self.state_lock:
                state = self.session_states.get(session_id)
                if state is not None:
                    state.inflight = False
                    state.touch()
                    if state.has_mailbox_events() and state.has_triggerable_mailbox_events():
                        should_requeue = not state.wakeup_queued
                        if should_requeue:
                            state.wakeup_queued = True
                    elif not state.has_mailbox_events():
                        self.session_states.pop(session_id, None)
            if should_requeue:
                await self.bus.publish(
                    RuntimeEvent(
                        type=EventType.WAKEUP_REQUESTED,
                        session_id=session_id,
                        payload={"reason": WakeupReason.SYSTEM.value},
                        source="event_bus",
                    )
                )

    async def heartbeat_watchdog(self) -> None:
        interval = max(5, settings.planner_heartbeat_seconds)
        threshold = float(settings.planner_heartbeat_seconds * 2)
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            sessions_to_wakeup: list[str] = []
            async with self.state_lock:
                stale = [state.session_id for state in self.session_states.values() if now - state.heartbeat_at > threshold]
                for state in self.session_states.values():
                    if not state.has_mailbox_events():
                        continue
                    if not state.has_triggerable_mailbox_events():
                        continue
                    if state.inflight:
                        continue
                    if state.wakeup_queued:
                        continue
                    state.wakeup_queued = True
                    sessions_to_wakeup.append(state.session_id)
            for session_id in stale:
                stale_for = 0
                async with self.state_lock:
                    stale_state = self.session_states.get(session_id)
                    if stale_state is not None:
                        stale_for = int(now - stale_state.heartbeat_at)
                logger.warning("Planner heartbeat stale for session=%s stale_for=%ss", session_id, stale_for)
            for session_id in sessions_to_wakeup:
                await self.bus.publish(
                    RuntimeEvent(
                        type=EventType.WAKEUP_REQUESTED,
                        session_id=session_id,
                        payload={"reason": WakeupReason.SYSTEM.value},
                        source="heartbeat_watchdog",
                    )
                )

    async def timer_watchdog(self) -> None:
        """Drive timer jobs by publishing synthetic user-message ticks."""
        interval_seconds = 1
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                due_jobs = await get_timer_runtime_store().claim_due_jobs(worker_id=self.timer_worker_id, limit=20)
            except Exception as exc:
                logger.warning("timer watchdog failed to claim jobs: %s", exc)
                continue
            for job in due_jobs:
                try:
                    payload_text = str(job.payload or "").strip()
                    tick_payload = {
                        "job_id": job.job_id,
                        "mode": job.mode.value,
                        "owner_session_id": job.owner_session_id,
                        "creator_session_id": job.creator_session_id,
                        "run_count": int(job.run_count),
                        "next_fire_at_ms": int(job.next_fire_at_ms),
                        "payload": payload_text,
                    }
                    internal_user_text = (
                        f"[TIMER_TICK] mode={job.mode.value} job_id={job.job_id} "
                        f"run_index={int(job.run_count) + 1}"
                    )
                    if payload_text:
                        internal_user_text = internal_user_text + f"\n[TIMER_PAYLOAD] {payload_text}"
                    await self.bus.publish(
                        RuntimeEvent(
                            type=EventType.USER_MESSAGE_RECEIVED,
                            session_id=job.owner_session_id,
                            payload={
                                "trigger_turn": True,
                                "task_type": "general",
                                "internal_user_text": internal_user_text,
                                "timer_tick": tick_payload,
                            },
                            source="timer_watchdog",
                        )
                    )
                    uwg = get_user_gateway()
                    if uwg is not None:
                        await uwg.emit_timer_tick(
                            job.owner_session_id,
                            job_id=job.job_id,
                            mode=job.mode.value,
                            owner_session_id=job.owner_session_id,
                            creator_session_id=job.creator_session_id,
                            run_index=int(job.run_count) + 1,
                            payload=payload_text,
                            next_fire_at_ms=int(job.next_fire_at_ms),
                        )
                    logger.info(
                        "Timer tick published: job_id=%s mode=%s owner=%s run_index=%s",
                        job.job_id,
                        job.mode.value,
                        job.owner_session_id,
                        int(job.run_count) + 1,
                    )
                    await get_timer_runtime_store().mark_job_result(job_id=job.job_id, success=True)
                except Exception as exc:
                    logger.exception("Timer tick processing failed: job_id=%s error=%s", job.job_id, exc)
                    await get_timer_runtime_store().mark_job_result(
                        job_id=job.job_id,
                        success=False,
                        error_message=f"timer tick processing failed: {exc}",
                    )

    async def on_event(self, event: RuntimeEvent) -> None:
        """Route runtime events through a single scheduler entrypoint."""
        normalized_type = event_type_value(event.type)
        if normalized_type == EventType.USER_MESSAGE_RECEIVED.value:
            await self._handle_user_message(event)
            return
        if normalized_type == EventType.SUBTASK_COMPLETED.value:
            await self._handle_subtask_completed(event)
            return
        if normalized_type == EventType.WAKEUP_REQUESTED.value:
            await self._handle_wakeup_requested(event)
            return
        await self.event_dispatcher.dispatch(event)

    async def _handle_user_message(self, event: RuntimeEvent) -> None:
        parsed = UserMessageReceivedEventPayload.from_runtime_event(event)
        trigger_turn = parsed.trigger_turn
        payload = event.payload if isinstance(event.payload, dict) else {}
        internal_user_text = payload.get("internal_user_text")
        if isinstance(internal_user_text, str) and internal_user_text.strip():
            try:
                async with AsyncSessionLocal() as db:
                    from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole
                    from fairyclaw.core.agent.session.memory import PersistentMemory
                    from fairyclaw.core.domain import ContentSegment
                    from fairyclaw.infrastructure.database.repository import EventRepository

                    memory = PersistentMemory(EventRepository(db))
                    msg = SessionMessageBlock.from_segments(
                        SessionMessageRole.USER,
                        (ContentSegment.text_segment(internal_user_text.strip()),),
                    )
                    if msg is not None:
                        await memory.add_session_event(session_id=event.session_id, message=msg)
            except Exception as exc:
                logger.warning(
                    "Failed to persist internal_user_text into session history: session=%s error=%s",
                    event.session_id,
                    exc,
                )
        schedule_debounce = False
        async with self.state_lock:
            state = self.get_or_create_state(event.session_id)
            state.enqueue_event(event)
            existing_task = self.debounce_tasks.get(event.session_id)
            if trigger_turn and (existing_task is None or existing_task.done()):
                schedule_debounce = True
        if schedule_debounce:
            self.debounce_tasks[event.session_id] = asyncio.create_task(
                self.run_user_message_debounce(event.session_id)
            )

    async def _handle_subtask_completed(self, event: RuntimeEvent) -> None:
        parsed = SubtaskCompletedEventPayload.from_runtime_event(event)
        trigger_turn = parsed.trigger_turn
        has_active_debounce = False
        async with self.state_lock:
            self.get_or_create_state(event.session_id).enqueue_event(event)
            debounce_task = self.debounce_tasks.get(event.session_id)
            has_active_debounce = debounce_task is not None and not debounce_task.done()
        if trigger_turn and not has_active_debounce:
            await self.request_wakeup_if_needed(
                session_id=event.session_id,
                reason=WakeupReason.SUBTASK_COMPLETED.value,
                source="subtask_completed",
            )
        uwg = get_user_gateway()
        if uwg is not None:
            asyncio.create_task(uwg.emit_subagent_tasks_snapshot(event.session_id))

    async def _handle_wakeup_requested(self, event: RuntimeEvent) -> None:
        parsed = WakeupRequestedEventPayload.from_runtime_event(event)
        await self.run_session(event.session_id, wakeup_source=event.source, wakeup_reason=parsed.reason)

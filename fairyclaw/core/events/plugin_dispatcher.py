# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime event dispatcher backed by capability plugin executors."""

from __future__ import annotations

import logging

from fairyclaw.core.agent.constants import SUB_SESSION_MARKER
from fairyclaw.core.agent.hooks.protocol import HookExecutionContext, HookStageInput
from fairyclaw.core.agent.hooks.runtime import HookRuntime
from fairyclaw.core.capabilities.registry import CapabilityRegistry
from fairyclaw.core.events.bus import RuntimeEvent, event_type_value
from fairyclaw.core.events.payloads import GenericRuntimeEventPayload, UserMessageReceivedEventPayload, payload_from_runtime_event

logger = logging.getLogger(__name__)


class EventPluginDispatcher:
    """Dispatch runtime events to plugin executors declared as hook scripts."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry
        self.runtime = HookRuntime()

    async def dispatch(self, event: RuntimeEvent) -> dict[str, object]:
        """Dispatch one runtime event by mapping it to an event hook stage."""
        event_type_name = event_type_value(event.type)
        stage_name = f"event:{event_type_name}"
        typed_payload = payload_from_runtime_event(event)
        if isinstance(typed_payload, GenericRuntimeEventPayload):
            event_type_def = self.registry.get_event_type_definition(event_type_name)
            if event_type_def is not None:
                typed_payload = GenericRuntimeEventPayload(
                    session_id=typed_payload.session_id,
                    event_id=typed_payload.event_id,
                    source=typed_payload.source,
                    timestamp_ms=typed_payload.timestamp_ms,
                    event_type=typed_payload.event_type,
                    data=typed_payload.data,
                    schema_definition=dict(event_type_def.schema_definition),
                )
        payload_groups = typed_payload.enabled_groups if isinstance(typed_payload, UserMessageReceivedEventPayload) else None
        if payload_groups is None:
            raw_groups = getattr(typed_payload, "enabled_groups", None)
            if isinstance(raw_groups, list):
                payload_groups = [group for group in raw_groups if isinstance(group, str) and group.strip()]
        declaring_groups = self.registry.get_groups_for_event_type(event_type_name)
        enabled_groups = payload_groups or declaring_groups or self.registry.resolve_enabled_groups(
            is_sub_session=(SUB_SESSION_MARKER in event.session_id)
        )
        hook_defs = self.registry.get_hooks(stage=stage_name, group_names=enabled_groups)
        bound: list[tuple[object, object]] = []
        for hook in hook_defs:
            executor = self.registry.get_hook_executor(hook.name)
            if executor is None:
                continue
            bound.append((hook, executor))
        if not bound:
            logger.info(
                "No event plugin registered for stage=%s session=%s",
                stage_name,
                event.session_id,
            )
            return {"status": "skip"}
        output = await self.runtime.run_stage(
            HookStageInput(
                stage=stage_name,
                context=HookExecutionContext(
                    session_id=event.session_id,
                    turn_id=f"event_{event.id[:8]}",
                    task_type="event_dispatch",
                    is_sub_session=SUB_SESSION_MARKER in event.session_id,
                    metadata={"event_type": event_type_name, "source": event.source},
                ),
                payload=typed_payload,
            ),
            bound,
        )
        return {"status": output.status.value, "artifacts": output.artifacts}

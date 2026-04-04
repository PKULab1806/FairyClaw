# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import json
from pathlib import Path

import fairyclaw.core.events.runtime as runtime_module
from fairyclaw.core.agent.hooks.protocol import HookStageOutput, HookStatus
from fairyclaw.core.capabilities.models import EventTypeDefinition, HookDefinition
from fairyclaw.core.capabilities.registry import CapabilityRegistry
from fairyclaw.core.events.bus import SessionEventBus
from fairyclaw.core.events.payloads import GenericRuntimeEventPayload, payload_from_runtime_event
from fairyclaw.core.events.plugin_dispatcher import EventPluginDispatcher
from fairyclaw.core.events.runtime import publish_runtime_event, set_runtime_bus
from fairyclaw.core.events.session_scheduler import RuntimeSessionScheduler


def test_capability_registry_loads_declared_custom_event_types(tmp_path: Path) -> None:
    group_dir = tmp_path / "custom_events"
    group_dir.mkdir()
    manifest = {
        "name": "CustomEvents",
        "description": "Custom runtime events for plugin dispatch.",
        "always_enable_planner": False,
        "always_enable_subagent": False,
        "manifest_version": "1.1",
        "capabilities": [],
        "hooks": [],
        "event_types": [
            {
                "name": "my_custom_event",
                "description": "Example custom event.",
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            }
        ],
    }
    (group_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    registry = CapabilityRegistry(str(tmp_path))

    assert registry.get_declared_event_types() == {"my_custom_event"}
    event_type_def = registry.get_event_type_definition("my_custom_event")
    assert event_type_def is not None
    assert event_type_def.schema_definition["required"] == ["answer"]


def test_payload_from_runtime_event_returns_generic_payload_for_custom_event() -> None:
    from fairyclaw.core.events.bus import RuntimeEvent

    payload = payload_from_runtime_event(
        RuntimeEvent(
            type="my_custom_event",
            session_id="sess_custom",
            payload={"answer": "42"},
            source="test_suite",
        )
    )

    assert isinstance(payload, GenericRuntimeEventPayload)
    assert payload.event_type == "my_custom_event"
    assert payload.data == {"answer": "42"}


class _RecordingRegistry:
    def __init__(self) -> None:
        self.captured_inputs: list[object] = []
        self._event_type_definition = EventTypeDefinition(
            name="my_custom_event",
            description="Example custom event.",
            schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        )
        self._hook = HookDefinition(
            name="custom_event_hook",
            stage="event:my_custom_event",
            script="custom_event_hook.py",
        )

    def get_declared_event_types(self, group_names: list[str] | None = None) -> set[str]:
        return {"my_custom_event"}

    def get_event_type_definition(self, event_type_name: str) -> EventTypeDefinition | None:
        if event_type_name == "my_custom_event":
            return self._event_type_definition
        return None

    def get_groups_for_event_type(self, event_type_name: str) -> list[str]:
        if event_type_name == "my_custom_event":
            return ["CustomEvents"]
        return []

    def resolve_enabled_groups(self, selected_groups: list[str] | None = None, is_sub_session: bool = False) -> list[str]:
        return ["RuntimeEventHooks"]

    def get_hooks(self, stage: str, group_names: list[str] | None = None) -> list[HookDefinition]:
        if stage == "event:my_custom_event" and group_names and "CustomEvents" in group_names:
            return [self._hook]
        return []

    def get_hook_executor(self, hook_name: str):
        async def _execute_hook(hook_input):
            self.captured_inputs.append(hook_input)
            return HookStageOutput(status=HookStatus.OK, patched_payload=hook_input.payload)

        return _execute_hook if hook_name == "custom_event_hook" else None


class _PlannerStub:
    def __init__(self, registry: _RecordingRegistry) -> None:
        self.registry = registry


def test_scheduler_dispatches_custom_event_without_planner_wakeup() -> None:
    async def _run() -> None:
        registry = _RecordingRegistry()
        bus = SessionEventBus(worker_count=1)
        dispatcher = EventPluginDispatcher(registry)
        scheduler = RuntimeSessionScheduler(bus=bus, planner=_PlannerStub(registry), event_dispatcher=dispatcher)
        original_bus = runtime_module.get_runtime_bus()
        try:
            set_runtime_bus(bus)
            await scheduler.start()
            published = await publish_runtime_event(
                "my_custom_event",
                session_id="sess_custom",
                payload={"answer": "42"},
                source="test_suite",
            )
            assert published is True

            for _ in range(20):
                if registry.captured_inputs:
                    break
                await asyncio.sleep(0.01)

            assert len(registry.captured_inputs) == 1
            hook_input = registry.captured_inputs[0]
            assert hook_input.stage == "event:my_custom_event"
            assert isinstance(hook_input.payload, GenericRuntimeEventPayload)
            assert hook_input.payload.data == {"answer": "42"}
            assert hook_input.payload.schema_definition == {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
            }
            assert scheduler.session_states == {}
        finally:
            await scheduler.stop()
            await bus.stop()
            runtime_module._runtime_bus = original_bus

    asyncio.run(_run())

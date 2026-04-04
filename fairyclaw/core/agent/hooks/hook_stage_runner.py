# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Hook stage runner decoupled from planner orchestration."""

from __future__ import annotations

from typing import TypeVar, cast

from fairyclaw.config import settings
from fairyclaw.core.agent.hooks.protocol import (
    HookExecutionContext,
    HookStage,
    HookStageInput,
    HookStageOutput,
    HookStatus,
)
from fairyclaw.core.agent.hooks.runtime import HookExecutor, HookRuntime
from fairyclaw.core.capabilities.models import HookDefinition
from fairyclaw.core.capabilities.registry import CapabilityRegistry

PayloadT = TypeVar("PayloadT")


class HookStageRunner:
    """Resolve and execute hooks for one lifecycle stage."""

    def __init__(self, registry: CapabilityRegistry, runtime: HookRuntime) -> None:
        self.registry = registry
        self.runtime = runtime

    async def run_stage(
        self,
        stage: HookStage | str,
        hook_context: HookExecutionContext,
        payload: PayloadT,
        enabled_groups: list[str] | None = None,
    ) -> HookStageOutput[PayloadT]:
        target_groups = enabled_groups if enabled_groups is not None else hook_context.enabled_groups
        if not settings.enable_hook_runtime:
            return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
        stage_name = stage.value if isinstance(stage, HookStage) else str(stage)
        hooks = self.registry.get_hooks(stage_name, group_names=target_groups)
        if not hooks:
            return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
        bound: list[tuple[HookDefinition, HookExecutor[PayloadT]]] = []
        for hook in hooks:
            executor = self.registry.get_hook_executor(hook.name)
            if executor is None:
                continue
            bound.append((hook, cast(HookExecutor[PayloadT], executor)))
        if not bound:
            return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
        return await self.runtime.run_stage(
            HookStageInput(stage=stage, context=hook_context, payload=payload),
            bound,
        )

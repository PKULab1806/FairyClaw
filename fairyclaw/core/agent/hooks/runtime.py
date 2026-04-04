# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Hook runtime executor and stage orchestrator."""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

from fairyclaw.core.agent.hooks.protocol import HookError, HookStageInput, HookStageOutput, HookStatus
from fairyclaw.core.capabilities.models import HookDefinition, HookErrorPolicy

PayloadT = TypeVar("PayloadT")

HookExecutor = Callable[[HookStageInput[PayloadT]], Awaitable[HookStageOutput[PayloadT] | None]]


class HookRuntime:
    """Run hook executors with timeout and error policy."""

    async def run_stage(
        self,
        hook_input: HookStageInput[PayloadT],
        hooks: list[tuple[HookDefinition, HookExecutor[PayloadT]]],
    ) -> HookStageOutput[PayloadT]:
        """Execute all hooks of one stage and return merged output."""
        merged_payload = hook_input.payload
        artifacts: dict[str, object] = {}
        metrics: dict[str, object] = {"hook_count": len(hooks)}
        stage_start = time.time()

        for hook_def, executor in hooks:
            if not hook_def.enabled:
                continue
            started = time.time()
            try:
                timeout = max(1, int(hook_def.timeout_ms)) / 1000.0
                raw = await asyncio.wait_for(
                    executor(
                        HookStageInput(
                            stage=hook_input.stage,
                            context=hook_input.context,
                            payload=merged_payload,
                            budget=hook_input.budget,
                            metadata=hook_input.metadata,
                        )
                    ),
                    timeout=timeout,
                )
                output = self._normalize_output(raw)
                if output.patched_payload is not None:
                    merged_payload = output.patched_payload
                if output.artifacts:
                    artifacts[hook_def.name] = output.artifacts
                metrics[f"{hook_def.name}_duration_ms"] = int((time.time() - started) * 1000)
            except Exception as exc:
                metrics[f"{hook_def.name}_duration_ms"] = int((time.time() - started) * 1000)
                if hook_def.on_error == HookErrorPolicy.FAIL:
                    return HookStageOutput(
                        status=HookStatus.ERROR,
                        patched_payload=merged_payload,
                        artifacts=artifacts,
                        metrics=metrics,
                        error=HookError(code="hook_failed", message=str(exc), retriable=False),
                    )
                if hook_def.on_error == HookErrorPolicy.WARN:
                    artifacts[f"{hook_def.name}_warning"] = {"error": str(exc)}
                continue

        metrics["stage_duration_ms"] = int((time.time() - stage_start) * 1000)
        return HookStageOutput(
            status=HookStatus.OK,
            patched_payload=merged_payload,
            artifacts=artifacts,
            metrics=metrics,
            error=None,
        )

    def _normalize_output(self, raw: HookStageOutput[PayloadT] | None) -> HookStageOutput[PayloadT]:
        if raw is None:
            return HookStageOutput(status=HookStatus.SKIP)
        return raw

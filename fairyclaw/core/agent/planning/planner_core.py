# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Shared planner core base class."""

from __future__ import annotations

import logging

from fairyclaw.config import settings
from fairyclaw.core.agent.context.llm_message_assembler import LlmMessageAssembler
from fairyclaw.core.agent.executors.context_pipeline import ContextPipelineExecutor
from fairyclaw.core.agent.executors.session_capability_resolver import SessionCapabilityResolver
from fairyclaw.core.agent.executors.tool_pipeline import ToolPipelineExecutor
from fairyclaw.core.agent.hooks.hook_stage_runner import HookStageRunner
from fairyclaw.core.agent.hooks.runtime import HookRuntime
from fairyclaw.core.agent.routing.router import ToolRouter
from fairyclaw.core.capabilities.registry import CapabilityRegistry
from fairyclaw.infrastructure.llm.factory import create_default_llm_client, create_llm_client
from fairyclaw.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


class BasePlanner:
    """Shared planner core dependencies and factory methods."""

    def __init__(self) -> None:
        self.llm_client = create_default_llm_client()
        capabilities_dir = settings.capabilities_dir
        self.registry = CapabilityRegistry(capabilities_dir)
        self.router = ToolRouter(self.registry)
        self.tool_runtime = ToolRuntime(self.registry)
        self.hook_runtime = HookRuntime()
        self.hook_stage_runner = HookStageRunner(self.registry, self.hook_runtime)
        self.context_pipeline = ContextPipelineExecutor()
        self.tool_pipeline = ToolPipelineExecutor()
        self.capability_resolver = SessionCapabilityResolver(self.registry)
        self.message_assembler = LlmMessageAssembler()

    def reload_llm_client(self) -> None:
        """Reload the default LLM client from disk after ``apply_llm_document``."""
        self.llm_client = create_default_llm_client()

    def resolve_llm_client(self, task_type: str):
        """Resolve LLM client by task type with graceful fallback."""
        if task_type == "general":
            return self.llm_client
        try:
            return create_llm_client(task_type)
        except RuntimeError:
            logger.warning("Profile '%s' not found, falling back to default main profile.", task_type)
            return self.llm_client

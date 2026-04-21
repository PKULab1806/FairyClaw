# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tool runtime: locate executors by capability name and execute them."""

import json
from typing import Any

from fairyclaw.core.capabilities.registry import CapabilityRegistry
from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.core.runtime.session_runtime_store import get_session_runtime_store
from fairyclaw.config.settings import settings as _settings


class ToolRuntime:
    """Runtime executor for tool capabilities."""

    def __init__(self, registry: CapabilityRegistry):
        """Bind capability registry for dynamic executor lookup.

        Args:
            registry (CapabilityRegistry): Loaded capability registry.
        """
        self.registry = registry

    async def execute(self, tool_name: str, arguments_json: str, session_id: str, memory: Any = None, planner: Any = None) -> str:
        """Execute one tool invocation and return a normalized text result.

        Args:
            tool_name (str): Target tool name.
            arguments_json (str): JSON argument payload for tool execution.
            session_id (str): Current session identifier.
            memory (Any): Optional memory service for tools that require persistence.
            planner (Any): Optional planner reference for tools requiring planner access.

        Returns:
            str: Tool result text or a standardized error message.
        """
        tool_executor = self.registry.get_tool_executor(tool_name)
        if tool_executor:
            try:
                arguments = json.loads(arguments_json) if isinstance(arguments_json, str) else arguments_json
            except json.JSONDecodeError:
                return "Error: Invalid JSON arguments."

            group_runtime_config: object | None = None
            for group in self.registry.groups.values():
                if any(t.name == tool_name for t in group.tools):
                    group_runtime_config = group.runtime_config
                    break
            context = ToolContext(
                session_id=session_id,
                memory=memory,
                planner=planner,
                group_runtime_config=group_runtime_config,
                filesystem_root_dir=_settings.filesystem_root_dir,
                workspace_root=None,
                runtime_context=None,
            )
            runtime_context = await get_session_runtime_store().get(session_id)
            context.workspace_root = runtime_context.workspace_root
            context.runtime_context = runtime_context

            try:
                result = await tool_executor(arguments, context)
                return result if isinstance(result, str) else str(result)
            except Exception as e:
                return f"Error executing tool '{tool_name}': {str(e)}"

        return f"Error: Tool '{tool_name}' not found."

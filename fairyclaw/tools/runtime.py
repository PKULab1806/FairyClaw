# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tool/skill runtime.

Locate executors by capability name and pass a unified execution context.
"""

import json
from typing import Any

from fairyclaw.core.capabilities.registry import CapabilityRegistry
from fairyclaw.core.capabilities.models import ToolContext

class SkillRuntime:
    """Runtime executor for tool and skill capabilities."""

    def __init__(self, registry: CapabilityRegistry):
        """Bind capability registry for dynamic executor lookup.

        Args:
            registry (CapabilityRegistry): Loaded capability registry.

        Returns:
            None
        """
        self.registry = registry

    async def execute(self, tool_name: str, arguments_json: str, session_id: str, memory: Any = None, planner: Any = None) -> str:
        """Execute one tool/skill invocation and return normalized text result.

        Args:
            tool_name (str): Target tool or skill name.
            arguments_json (str): JSON argument payload for tool execution.
            session_id (str): Current session identifier.
            memory (Any): Optional memory service for tools that require persistence.
            planner (Any): Optional planner reference for tools requiring planner access.

        Returns:
            str: Tool result text, skill steps text, or standardized error message.

        Raises:
            Tool execution exceptions are caught and converted to error strings.
        """
        tool_executor = self.registry.get_tool_executor(tool_name)
        if tool_executor:
            try:
                if isinstance(arguments_json, str):
                    arguments = json.loads(arguments_json)
                else:
                    arguments = arguments_json
            except json.JSONDecodeError:
                return "Error: Invalid JSON arguments."

            context = ToolContext(session_id=session_id, memory=memory, planner=planner)

            try:
                result = await tool_executor(arguments, context)
                if not isinstance(result, str):
                    return str(result)
                return result
            except Exception as e:
                return f"Error executing tool '{tool_name}': {str(e)}"

        if tool_name in self.registry.skills:
            skill = self.registry.skills[tool_name]
            if skill.steps:
                steps_formatted = "\n".join([f"{i+1}. {step}" for i, step in enumerate(skill.steps)])
                return f"Skill '{tool_name}' Steps:\n{steps_formatted}"
            return "No specific steps defined for this skill."
            
        return f"Error: Tool or Skill '{tool_name}' not found."

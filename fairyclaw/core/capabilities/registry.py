# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Capability registry.

Scan group manifests, dynamically load tool scripts, and build LLM tool schemas.
"""

import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from fairyclaw.core.agent.hooks.protocol import EventHookHandler, HookStageInput, HookStageOutput
from fairyclaw.core.capabilities.models import (
    CapabilityGroup,
    EventTypeDefinition,
    HookDefinition,
    SkillCapability,
    ToolCapability,
    ToolContext,
)

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Load capability manifests, executors, and tool schemas."""

    def __init__(self, capabilities_dir: str):
        """Initialize registry and eagerly load capability metadata/executors.

        Args:
            capabilities_dir (str): Root directory containing capability group folders.

        Returns:
            None
        """
        self.capabilities_dir = Path(capabilities_dir)
        self.groups: Dict[str, CapabilityGroup] = {}
        self.tools: Dict[str, ToolCapability] = {}
        self.skills: Dict[str, SkillCapability] = {}
        self.event_types: Dict[str, EventTypeDefinition] = {}
        self.hooks: Dict[str, list[HookDefinition]] = {}
        self.tool_executors: Dict[str, Callable[[Dict[str, Any], ToolContext], Any]] = {}
        self.hook_executors: Dict[str, Callable[[HookStageInput[object]], Awaitable[HookStageOutput[object] | None]]] = {}
        self._load_capabilities()

    def _load_capabilities(self):
        """Scan capability directories and register tools/skills.

        Returns:
            None

        Raises:
            Manifest parsing/loading errors are caught per group and logged.
        """
        if not self.capabilities_dir.exists():
            return

        for group_dir in self.capabilities_dir.iterdir():
            if group_dir.is_dir():
                manifest_path = group_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest_data = json.load(f)
                        
                        group = CapabilityGroup(**manifest_data)
                        self.groups[group.name] = group
                        
                        # Register Tools
                        for tool_def in group.tools:
                            self.tools[tool_def.name] = tool_def
                            if tool_def.script:
                                script_path = (group_dir / "scripts" / tool_def.script).resolve()
                                self._load_tool_executor(tool_def.name, script_path)
                        
                        # Register Skills
                        for skill_def in group.skills:
                            self.skills[skill_def.name] = skill_def
                        # Register custom runtime events
                        for event_type_def in group.event_type_definitions:
                            self.event_types[event_type_def.name] = event_type_def
                        # Register Hooks
                        self.hooks[group.name] = group.hook_definitions
                        for hook_def in group.hook_definitions:
                            script_path = (group_dir / "scripts" / hook_def.script).resolve()
                            self._load_hook_executor(hook_def, script_path)
                            
                    except Exception as e:
                        logger.error(f"Error loading capabilities from {group_dir}: {e}")

    def _load_tool_executor(self, tool_name: str, script_path: Path):
        """Dynamically load execute() function from tool script.

        Args:
            tool_name (str): Tool name defined in manifest.
            script_path (Path): Resolved script path.

        Returns:
            None

        Raises:
            Module loading errors are caught and logged.
        """
        if not script_path.exists():
            logger.error(f"Script not found for tool {tool_name}: {script_path}")
            return

        try:
            module_name = f"capabilities.tools.{tool_name}"
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                if hasattr(module, "execute"):
                    self.tool_executors[tool_name] = module.execute
                else:
                    logger.error(f"Script for tool {tool_name} does not have an 'execute' function.")
        except Exception as e:
            logger.error(f"Error loading script for tool {tool_name}: {e}")

    def _load_hook_executor(self, hook_def: HookDefinition, script_path: Path) -> None:
        """Dynamically load execute_hook() or execute() from hook script."""
        hook_name = hook_def.name
        if not script_path.exists():
            logger.error(f"Script not found for hook {hook_name}: {script_path}")
            return
        try:
            module_name = f"capabilities.hooks.{hook_name}"
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                handler_cls = self._find_event_hook_handler_class(module)
                if handler_cls is not None:
                    expected_event_type = hook_def.stage.removeprefix("event:")
                    handler = handler_cls()
                    raw_event_type = getattr(handler, "event_type", "")
                    event_type_value = getattr(raw_event_type, "value", raw_event_type)
                    if str(event_type_value) != expected_event_type:
                        logger.error(
                            "Event hook %s has mismatched event_type=%s, expected=%s",
                            hook_name,
                            event_type_value,
                            expected_event_type,
                        )
                        return
                    self.hook_executors[hook_name] = lambda hook_input, _h=handler: _h.run(
                        hook_input.payload, hook_input.context
                    )
                elif hasattr(module, "execute_hook"):
                    self.hook_executors[hook_name] = module.execute_hook
                elif hasattr(module, "execute"):
                    self.hook_executors[hook_name] = module.execute
                else:
                    logger.error(f"Script for hook {hook_name} lacks execute_hook/execute.")
        except Exception as e:
            logger.error(f"Error loading script for hook {hook_name}: {e}")

    def _find_event_hook_handler_class(self, module: object) -> type[EventHookHandler] | None:
        """Find first EventHookHandler subclass defined in a module."""
        for _, candidate in inspect.getmembers(module, inspect.isclass):
            if not issubclass(candidate, EventHookHandler) or candidate is EventHookHandler:
                continue
            if candidate.__module__ != getattr(module, "__name__", ""):
                continue
            return candidate
        return None

    def get_tool_executor(self, tool_name: str) -> Optional[Callable[[Dict[str, Any], ToolContext], Any]]:
        """Get registered executor for a tool.

        Args:
            tool_name (str): Tool name.

        Returns:
            Optional[Callable[[Dict[str, Any], ToolContext], Any]]: Executor function or None.
        """
        return self.tool_executors.get(tool_name)

    def get_hook_executor(
        self, hook_name: str
    ) -> Optional[Callable[[HookStageInput[object]], Awaitable[HookStageOutput[object] | None]]]:
        """Get registered executor for a hook."""
        return self.hook_executors.get(hook_name)

    def get_group_profiles(self) -> List[Dict[str, Any]]:
        """Build lightweight group profiles for router selection.

        Returns:
            List[Dict[str, Any]]: Group metadata list including descriptions and tool names.
        """
        profiles = []
        for name, group in self.groups.items():
            tool_names = [t.name for t in group.tools] + [s.name for s in group.skills]
            profiles.append({
                "group_name": group.name,
                "description": group.description,
                "always_enable_planner": group.always_enable_planner,
                "always_enable_subagent": group.always_enable_subagent,
                "contains_tools": tool_names
            })
        return profiles

    def resolve_enabled_groups(
        self,
        selected_groups: Optional[List[str]] = None,
        is_sub_session: bool = False,
    ) -> List[str]:
        """Resolve enabled groups for planner or sub-agent session."""
        always = [
            name
            for name, g in self.groups.items()
            if (g.always_enable_subagent if is_sub_session else g.always_enable_planner)
        ]
        if selected_groups is None:
            return list(dict.fromkeys(always))
        filtered = [g for g in selected_groups if g in self.groups]
        return list(dict.fromkeys(always + filtered))

    def get_hooks(self, stage: str, group_names: Optional[List[str]] = None) -> List[HookDefinition]:
        """Get hooks for given stage filtered by enabled groups."""
        target_names = group_names or list(self.groups.keys())
        result: list[HookDefinition] = []
        for group_name in target_names:
            for hook in self.hooks.get(group_name, []):
                if hook.stage == stage and hook.enabled:
                    result.append(hook)
        result.sort(key=lambda item: (item.priority, item.name))
        return result

    def get_declared_event_types(self, group_names: Optional[List[str]] = None) -> set[str]:
        """Return declared custom runtime event names."""
        target_groups = group_names or list(self.groups.keys())
        declared: set[str] = set()
        for group_name in target_groups:
            group = self.groups.get(group_name)
            if group is None:
                continue
            for event_type_def in group.event_type_definitions:
                declared.add(event_type_def.name)
        return declared

    def get_event_type_definition(self, event_type_name: str) -> EventTypeDefinition | None:
        """Return one declared custom runtime event definition by name."""
        return self.event_types.get(event_type_name)

    def get_groups_for_event_type(self, event_type_name: str) -> list[str]:
        """Return capability groups that declare one custom runtime event."""
        matched: list[str] = []
        for group_name, group in self.groups.items():
            if any(event_type_def.name == event_type_name for event_type_def in group.event_type_definitions):
                matched.append(group_name)
        return matched

    def get_openai_tools(self, group_names: Optional[List[str]] = None, exclude_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Build OpenAI-compatible tool definitions.

        Args:
            group_names (Optional[List[str]]): Optional whitelist of capability groups.
            exclude_tools (Optional[List[str]]): Optional blacklist of tool/skill names.

        Returns:
            List[Dict[str, Any]]: OpenAI-style function tool schemas.
        """
        tools_schema = []
        exclude_tools = exclude_tools or []

        target_groups: Iterable[CapabilityGroup] = self.groups.values()
        if group_names is not None:
            target_groups = [g for g in self.groups.values() if g.name in group_names]
            
        allowed_tool_names = set()
        allowed_skill_names = set()
        
        for g in target_groups:
            for t in g.tools:
                allowed_tool_names.add(t.name)
            for s in g.skills:
                allowed_skill_names.add(s.name)
        
        for name, tool in self.tools.items():
            if name in exclude_tools:
                continue
            if group_names is not None and name not in allowed_tool_names:
                continue
            schema = tool.schema_definition.copy()
            
            function_def = {
                "name": name,
                "description": tool.description,
                "parameters": schema.get("parameters", {})
            }
            
            tools_schema.append({
                "type": "function",
                "function": function_def
            })
            
        for name, skill in self.skills.items():
            if name in exclude_tools:
                continue
            if group_names is not None and name not in allowed_skill_names:
                continue
            schema = skill.schema_definition.copy() if skill.schema_definition else {"parameters": {"type": "object", "properties": {}}}
            
            function_def = {
                "name": name,
                "description": skill.description,
                "parameters": schema.get("parameters", {})
            }
            
            tools_schema.append({
                "type": "function",
                "function": function_def
            })
            
        return tools_schema


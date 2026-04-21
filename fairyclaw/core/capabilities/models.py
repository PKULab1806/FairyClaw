# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Capability models and tool context definitions."""

from dataclasses import dataclass
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, cast

from pydantic import BaseModel, Field
from fairyclaw.core.runtime.session_runtime_store import SessionRuntimeContext

@dataclass
class ToolContext:
    """Carry runtime dependencies passed to tool executors.

    Attributes:
        session_id (str): Current session identifier.
        memory (Any): Memory service used by tools requiring persistence.
        planner (Any): Optional planner instance for advanced orchestration tools.
        group_runtime_config (Any | None): Frozen group-specific runtime config
            snapshot injected by the registry at load time.  Access via
            ``fairyclaw.sdk.group_runtime.expect_group_config``.
    """

    session_id: str
    memory: Any
    planner: Any = None
    group_runtime_config: Any = None
    filesystem_root_dir: str | None = None
    workspace_root: str | None = None
    runtime_context: SessionRuntimeContext | None = None


@dataclass(frozen=True)
class SafeFilesystemPath:
    """Safe path model constrained by an allowed root directory."""

    path: str
    root: str

    @classmethod
    def resolve(cls, path: str, root_dir: str, base_dir: str | None = None) -> "SafeFilesystemPath":
        """Resolve and normalize target path and root path.

        Args:
            path (str): Raw target path.
            root_dir (str): Allowed root directory.

        Returns:
            SafeFilesystemPath: Normalized path/root pair.
        """
        target = path
        if base_dir and not os.path.isabs(target):
            target = os.path.join(base_dir, target)
        resolved_path = os.path.realpath(os.path.abspath(target))
        resolved_root = os.path.realpath(os.path.abspath(root_dir))
        return cls(path=resolved_path, root=resolved_root)

    def is_within_root(self) -> bool:
        """Check whether target path is inside allowed root.

        Uses path containment (not string prefix alone) so a root of ``/`` works:
        ``startswith(root + sep)`` would require paths to begin with ``//``, which wrongly
        rejects every normal absolute path on POSIX.

        Returns:
            bool: True when path is equal to root or its descendant.
        """
        try:
            p = Path(self.path)
            r = Path(self.root)
            return p == r or p.is_relative_to(r)
        except (OSError, ValueError):
            return False

    def access_denied_error(self) -> str:
        """Build standard access-denied error message.

        Returns:
            str: Human-readable permission error text.
        """
        return (
            f"Path {self.path!r} is outside FAIRYCLAW_FILESYSTEM_ROOT_DIR={self.root!r}. "
            "Filesystem tools only work under that directory; set it in config/fairyclaw.env "
            "to a folder that contains the paths you want the agent to use (for example your checkout root)."
        )


@dataclass(frozen=True)
class FileSystemListItem:
    """Represent one filesystem listing entry."""

    name: str
    item_type: str
    size: int
    path: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert listing item to dictionary payload.

        Returns:
            Dict[str, Any]: JSON-compatible listing entry.
        """
        return {
            "name": self.name,
            "type": self.item_type,
            "size": self.size,
            "path": self.path,
        }


@dataclass(frozen=True)
class CallbackPayload:
    """Represent callback payload sent to external user endpoint."""

    session_id: str
    role: str
    content: str
    message_type: str

    def to_dict(self) -> Dict[str, str]:
        """Convert callback payload to dictionary.

        Returns:
            Dict[str, str]: JSON-compatible callback payload.
        """
        return {
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "type": self.message_type,
        }


@dataclass(frozen=True)
class SessionFileListItem:
    """Represent summary metadata for a stored session file."""

    file_id: str
    filename: str
    size: int
    mime_type: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert file summary to dictionary payload.

        Returns:
            Dict[str, Any]: JSON-compatible file summary.
        """
        return {
            "id": self.file_id,
            "filename": self.filename,
            "size": self.size,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class ToolResultMessage:
    """Represent normalized structured tool result."""

    status: str
    message: str
    file_id: str | None = None

    def to_dict(self) -> Dict[str, str]:
        """Convert result message to dictionary payload.

        Returns:
            Dict[str, str]: JSON-compatible tool result mapping.
        """
        data: Dict[str, str] = {
            "status": self.status,
            "message": self.message,
        }
        if self.file_id is not None:
            data["file_id"] = self.file_id
        return data

    def to_json(self) -> str:
        """Serialize result message into JSON text.

        Returns:
            str: Serialized JSON string.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)


def resolve_safe_path(
    path: str,
    root_dir: str | None,
    workspace_root: str | None = None,
) -> tuple[SafeFilesystemPath | None, str | None]:
    """Resolve and validate a path against configured allowed roots.

    Args:
        path (str): Raw target path.
        root_dir (str | None): Global filesystem root directory.
        workspace_root (str | None): Session workspace root directory.

    Returns:
        tuple[SafeFilesystemPath | None, str | None]: Resolved safe path and optional error message.
    """
    allowed_roots: list[str] = []
    if isinstance(root_dir, str) and root_dir.strip():
        allowed_roots.append(root_dir.strip())
    if isinstance(workspace_root, str) and workspace_root.strip():
        wr = workspace_root.strip()
        if wr not in allowed_roots:
            allowed_roots.append(wr)
    if not allowed_roots:
        return None, "Error: Neither FAIRYCLAW_FILESYSTEM_ROOT_DIR nor workspace_root is configured."

    base_dir = workspace_root.strip() if isinstance(workspace_root, str) and workspace_root.strip() else None
    for allowed_root in allowed_roots:
        safe_path = SafeFilesystemPath.resolve(path, allowed_root, base_dir=base_dir)
        if safe_path.is_within_root():
            return safe_path, None

    roots_msg = ", ".join(repr(os.path.realpath(os.path.abspath(r))) for r in allowed_roots)
    return None, f"Path {path!r} is outside allowed roots: {roots_msg}."


def _memory_with_repo_db(memory: Any) -> Any | None:
    """Follow to PersistentMemory (or wrappers that expose .repo.db) to find DB session."""
    seen: set[int] = set()
    current: Any = memory
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        repo = getattr(current, "repo", None)
        if repo is not None and hasattr(repo, "db"):
            return current
        base = getattr(current, "_base", None)
        if base is None:
            break
        current = base
    return None


def get_context_db(context: ToolContext) -> tuple[Any, str | None]:
    """Extract DB session from tool context memory adapter.

    Args:
        context (ToolContext): Tool runtime context.

    Returns:
        tuple[Any, str | None]: Database session and optional error string.
    """
    if not context.memory:
        return cast(Any, None), "Error: Memory access required for file operations."
    inner = _memory_with_repo_db(context.memory)
    if inner is None:
        return cast(Any, None), "Error: Memory access required for file operations."
    return cast(Any, inner.repo.db), None


class CapabilityBase(BaseModel):
    """Base capability schema shared by tools and hooks."""

    name: str
    description: str
    type: str

class ToolCapability(CapabilityBase):
    """Define one tool capability loaded from manifest."""

    type: str = "Tool"
    schema_definition: Dict[str, Any] = Field(..., alias="schema")
    script: str
    record_event: bool = True


class HookErrorPolicy(str, Enum):
    """Error policy for hook failures."""

    CONTINUE = "continue"
    FAIL = "fail"
    WARN = "warn"


class HookCapability(CapabilityBase):
    """Define one hook capability loaded from manifest."""

    type: str = "Hook"
    stage: str
    script: str
    priority: int = 100
    enabled: bool = True
    timeout_ms: int = 300
    on_error: HookErrorPolicy = HookErrorPolicy.CONTINUE
    config: Dict[str, Any] = Field(default_factory=dict)


class HookDefinition(BaseModel):
    """Top-level hook definition for capability group."""

    name: str
    stage: str
    script: str
    priority: int = 100
    enabled: bool = True
    timeout_ms: int = 300
    on_error: HookErrorPolicy = HookErrorPolicy.CONTINUE
    config: Dict[str, Any] = Field(default_factory=dict)


class EventTypeDefinition(BaseModel):
    """Define one custom runtime event declared by a capability group."""

    name: str
    description: str = ""
    schema_definition: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        alias="schema",
    )

class CapabilityGroup(BaseModel):
    """Define one capability group and contained capabilities."""

    name: str
    description: str
    always_enable_planner: bool
    always_enable_subagent: bool
    manifest_version: str = "1.0"
    routing_hint: str | None = None
    capabilities: List[Dict[str, Any]]
    hooks: List[Dict[str, Any]] = Field(default_factory=list)
    event_types: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_config: Any = Field(default=None, exclude=True)
    
    @property
    def tools(self) -> List[ToolCapability]:
        """Return tool capabilities in this group.

        Returns:
            List[ToolCapability]: Parsed tool capability list.
        """
        return [ToolCapability(**c) for c in self.capabilities if c.get("type") == "Tool"]

    @property
    def hook_capabilities(self) -> List[HookCapability]:
        """Return hook capabilities declared in capabilities array."""
        return [HookCapability(**c) for c in self.capabilities if c.get("type") == "Hook"]

    @property
    def hook_definitions(self) -> List[HookDefinition]:
        """Return merged hook definitions from top-level and capability entries."""
        defs: list[HookDefinition] = []
        for item in self.hooks:
            defs.append(HookDefinition(**item))
        for item in self.hook_capabilities:
            defs.append(
                HookDefinition(
                    name=item.name,
                    stage=item.stage,
                    script=item.script,
                    priority=item.priority,
                    enabled=item.enabled,
                    timeout_ms=item.timeout_ms,
                    on_error=item.on_error,
                    config=item.config,
                )
            )
        return defs

    @property
    def event_type_definitions(self) -> List[EventTypeDefinition]:
        """Return declared custom runtime event definitions."""
        definitions: list[EventTypeDefinition] = []
        for item in self.event_types:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    definitions.append(EventTypeDefinition(name=name))
                continue
            if isinstance(item, dict):
                definitions.append(EventTypeDefinition(**item))
        return definitions

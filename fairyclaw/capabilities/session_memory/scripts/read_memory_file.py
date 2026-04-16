from __future__ import annotations

from typing import Any

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext
from fairyclaw_plugins.session_memory.config import SessionMemoryRuntimeConfig

from ._memory_files import read_memory_text


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    cfg = expect_group_config(context, SessionMemoryRuntimeConfig)
    name = str(args.get("name") or "").strip()
    try:
        text = read_memory_text(name=name, memory_root=cfg.memory_root)
    except ValueError as exc:
        return f"Error: {exc}"
    return text or "(empty)"

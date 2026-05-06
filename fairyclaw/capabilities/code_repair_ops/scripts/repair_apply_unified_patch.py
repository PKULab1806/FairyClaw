from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext
try:
    from fairyclaw_plugins.code_repair_ops.config import CodeRepairOpsRuntimeConfig
except ModuleNotFoundError:
    from fairyclaw.capabilities.code_repair_ops.config import CodeRepairOpsRuntimeConfig

from ._state import (
    file_hash,
    is_protected_path,
    load_session_meta,
    load_state,
    resolve_path,
    resolve_workspace_root,
    save_state,
)


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    cfg = expect_group_config(context, CodeRepairOpsRuntimeConfig)
    file_path = str(args.get("file_path") or "").strip()
    patch_text = str(args.get("patch_text") or "")
    if not file_path or not patch_text:
        return "Error: file_path and patch_text are required."
    meta = await load_session_meta(context.session_id)
    state = await load_state(context.session_id)
    workspace_root = resolve_workspace_root(meta, context.workspace_root)
    path = resolve_path(workspace_root, file_path)
    if not path.is_file():
        return f"Error: target file not found: {path}"
    if is_protected_path(path, workspace_root, list(cfg.protected_file_globs)):
        return f"Error: protected path cannot be edited: {path}"

    source = path.read_text(encoding="utf-8", errors="replace")
    # For existing files, reject pure append-only patches to avoid blind function duplication.
    if source and not any(line.startswith("-") for line in patch_text.splitlines()):
        return "Error: unified patch must contain at least one removal line for existing files."
    updated, detail = _apply_patch(source, patch_text)
    if updated is None:
        return f"Error: failed to apply patch: {detail}"
    changed_lines = _changed_line_count(source, updated)
    if changed_lines > int(cfg.max_changed_lines):
        return (
            "Error: changed lines exceed max policy. "
            f"changed_lines={changed_lines}, max_changed_lines={cfg.max_changed_lines}"
        )
    path.write_text(updated, encoding="utf-8")
    state["phase"] = "verify"
    state["last_tool_name"] = "repair_apply_unified_patch"
    state["last_tool_status"] = "ok"
    await save_state(context.session_id, state)
    return json.dumps(
        {"ok": True, "file_path": str(path), "changed_lines": changed_lines, "new_hash": file_hash(path)},
        ensure_ascii=False,
    )


def _apply_patch(source: str, patch_text: str) -> tuple[str | None, str]:
    lines = source.splitlines(keepends=True)
    patch_lines = patch_text.splitlines()
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in patch_lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
                current = []
            current.append(line)
            continue
        if current:
            current.append(line)
    if current:
        hunks.append(current)
    if not hunks:
        return None, "patch has no @@ hunks"

    cursor = 0
    for hunk in hunks:
        body = hunk[1:]
        expected_old: list[str] = []
        replacement_new: list[str] = []
        for line in body:
            if line.startswith(" "):
                text = line[1:]
                expected_old.append(text)
                replacement_new.append(text)
            elif line.startswith("-"):
                expected_old.append(line[1:])
            elif line.startswith("+"):
                replacement_new.append(line[1:])
            elif line and not line.startswith("@@"):
                # Accept loose hunk lines without prefix as context (common model output).
                expected_old.append(line)
                replacement_new.append(line)

        has_removals = any(line.startswith("-") for line in body)
        has_additions = any(line.startswith("+") for line in body)
        if not has_removals and not has_additions:
            continue

        if has_removals:
            block_idx = _find_sequence(lines, expected_old, cursor)
            if block_idx < 0:
                block_idx = _find_sequence(lines, expected_old, 0)
            if block_idx < 0:
                return None, "removal lines do not match target file"
            replacement_lines = [l + "\n" for l in replacement_new]
            lines[block_idx : block_idx + len(expected_old)] = replacement_lines
            cursor = block_idx + len(replacement_lines)
            continue

        # Pure insertion hunk: anchor using existing unchanged lines.
        anchor_idx = _find_anchor(lines, expected_old, cursor)
        if anchor_idx < 0:
            anchor_idx = max(0, min(cursor, len(lines)))
        add_lines = [l[1:] + "\n" for l in body if l.startswith("+")]
        lines[anchor_idx:anchor_idx] = add_lines
        cursor = anchor_idx + len(add_lines)
    return "".join(lines), "ok"


def _find_anchor(lines: list[str], context: list[str], start: int) -> int:
    if not context:
        return max(0, min(start, len(lines)))
    normalized = [l.rstrip("\n") for l in lines]
    needle = [c.rstrip("\n") for c in context]
    return _find_sequence(normalized, needle, start)


def _find_sequence(lines: list[str], seq: list[str], start: int) -> int:
    if not seq:
        return start
    normalized = [l.rstrip("\n") for l in lines]
    needle = [s.rstrip("\n") for s in seq]
    last = len(normalized) - len(needle)
    for i in range(max(0, start), last + 1):
        if normalized[i : i + len(needle)] == needle:
            return i
    return -1


def _changed_line_count(before: str, after: str) -> int:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    max_len = max(len(before_lines), len(after_lines))
    changed = 0
    for i in range(max_len):
        b = before_lines[i] if i < len(before_lines) else None
        a = after_lines[i] if i < len(after_lines) else None
        if b != a:
            changed += 1
    return changed


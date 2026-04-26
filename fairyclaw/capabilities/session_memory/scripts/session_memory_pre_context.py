from __future__ import annotations

import json
import logging
from dataclasses import replace

from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SessionMessageBlock, ToolCallRound
from fairyclaw.core.agent.hooks.protocol import BeforeLlmCallHookPayload, HookStageInput, HookStageOutput, HookStatus
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal
from fairyclaw.infrastructure.llm.factory import create_llm_client
from fairyclaw_plugins.session_memory.config import SessionMemoryRuntimeConfig

from ._extraction_checkpoint_state import LEGACY_MEMORY_CHECKPOINT_PREFIX
from ._gap_repair_state import load_gap_repair_state, save_gap_repair_state
from ._memory_files import read_memory_text

logger = logging.getLogger(__name__)


async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    payload = hook_input.payload
    cfg = SessionMemoryRuntimeConfig.model_validate(_load_group_config())
    turn = payload.turn
    if not turn.llm_messages:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    blocks: list[str] = []

    file_block = _build_file_block(cfg)
    if file_block:
        blocks.append(file_block)

    gap_patch = await _build_gap_repair_patch(
        session_id=hook_input.context.session_id,
        hook_input=hook_input,
        cfg=cfg,
    )
    if gap_patch:
        blocks.append(gap_patch)

    if not blocks:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    system0 = turn.llm_messages[0]
    if system0.role != "system":
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    existing = str(system0.content or "")
    merged_content = _merge_system_memory_block(existing, "\n\n".join(blocks))
    patched_messages = list(turn.llm_messages)
    patched_messages[0] = replace(system0, content=merged_content)
    patched = replace(payload, turn=replace(turn, llm_messages=patched_messages))
    return HookStageOutput(status=HookStatus.OK, patched_payload=patched)


def _load_group_config() -> dict[str, object]:
    from fairyclaw.config.loader import load_yaml
    from pathlib import Path

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        return load_yaml(config_path)
    return {}


def _build_file_block(cfg: SessionMemoryRuntimeConfig) -> str:
    parts: list[str] = []
    for name in ("USER.md", "SOUL.md", "MEMORY.md"):
        text = read_memory_text(name=name, memory_root=cfg.memory_root).strip()
        if not text:
            continue
        if name == "MEMORY.md":
            text = _sanitize_memory_for_prompt(text)
            if not text:
                continue
        snippet = text[-1400:] if name == "MEMORY.md" else text[-900:]
        parts.append(f"[{name}]\n{snippet}\n[/{name}]")
    if not parts:
        return ""
    return "[MemoryFilesContext]\n" + "\n\n".join(parts) + "\n[/MemoryFilesContext]"


async def _build_gap_repair_patch(
    *,
    session_id: str,
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
    cfg: SessionMemoryRuntimeConfig,
) -> str:
    async with AsyncSessionLocal() as db:
        memory = PersistentMemory(EventRepository(db))
        full_history = await memory.get_history(hook_input.context.session_id, limit=cfg.compaction_max_history_items)
    compressed = list(hook_input.payload.turn.history_items)
    if not full_history or not compressed:
        return ""
    full_history = _strip_current_user_turn(full_history, hook_input.payload)
    if len(full_history) <= len(compressed):
        return ""

    cut = max(0, len(full_history) - len(compressed))
    if cut < max(1, int(cfg.min_gap_repair_cut_items)):
        return ""
    end = min(len(full_history), cut + cfg.gap_headroom_items)
    state = load_gap_repair_state(session_id=session_id, memory_root=cfg.memory_root)
    last_end = int(state.get("last_slice_exclusive_end", 0) or 0)
    last_summary = str(state.get("last_summary", "") or "")

    # If this turn's slice does not extend beyond the last summarized prefix, reuse
    # the previous gap-repair note (headroom already covered) and skip another LLM call.
    if end <= last_end and last_summary.strip():
        return f"[GapRepairContext]\n{last_summary.strip()}\n[/GapRepairContext]"

    slice_items = full_history[:end]
    summary = await _summarize_history_slice(
        slice_items=slice_items,
        cfg=cfg,
        previous_gap_repair_summary=last_summary.strip() or None,
    )
    if not summary:
        return ""

    try:
        save_gap_repair_state(
            session_id=session_id,
            memory_root=cfg.memory_root,
            last_slice_exclusive_end=end,
            last_summary=summary,
        )
    except Exception as exc:
        logger.debug("gap repair state save skipped: %s", exc)
    return f"[GapRepairContext]\n{summary}\n[/GapRepairContext]"


async def _summarize_history_slice(
    *,
    slice_items: list[ChatHistoryItem],
    cfg: SessionMemoryRuntimeConfig,
    previous_gap_repair_summary: str | None = None,
) -> str:
    transcript = _history_to_transcript(slice_items)
    if not transcript:
        return ""
    prior_block = ""
    if previous_gap_repair_summary:
        prior_block = (
            "Previous gap-repair note for this session (same conversation; may overlap).\n"
            "Update only if the transcript adds material beyond it; otherwise tighten without repeating.\n\n"
            f"{previous_gap_repair_summary}\n\n---\n\n"
        )
    prompt = (
        "Summarize trimmed conversation context for gap repair after context compression.\n"
        "Preserve key user constraints, decisions, failures, and unfinished tasks.\n"
        "Keep under 8 bullet points. Do not ask questions. Do not include emojis or chit-chat.\n"
        "Return plain concise markdown only.\n\n"
        f"{prior_block}"
        f"{transcript}"
    )
    try:
        client = create_llm_client(cfg.compaction_profile)
        text = await client.chat(
            messages=[
                {"role": "system", "content": "You write concise, factual memory patches."},
                {"role": "user", "content": prompt},
            ]
        )
        return str(text).strip()[: cfg.summary_char_limit]
    except Exception:
        return transcript[: cfg.summary_char_limit]


def _history_to_transcript(items: list[ChatHistoryItem]) -> str:
    lines: list[str] = []
    for item in items:
        if isinstance(item, SessionMessageBlock):
            t = item.as_plain_text().strip()
            if t:
                lines.append(f"{item.role.value}: {t}")
        elif isinstance(item, ToolCallRound):
            lines.append(
                "tool: "
                + json.dumps(
                    {
                        "tool": item.tool_name,
                        "args": item.arguments_json,
                        "result": str(item.tool_result)[:400],
                    },
                    ensure_ascii=False,
                )
            )
    return "\n".join(lines)


def _merge_system_memory_block(system_prompt: str, memory_block: str) -> str:
    start = "[SessionMemory]"
    end = "[/SessionMemory]"
    if start in system_prompt and end in system_prompt:
        prefix = system_prompt.split(start, 1)[0].rstrip()
        suffix = system_prompt.split(end, 1)[1].lstrip()
        return f"{prefix}\n\n{start}\n{memory_block}\n{end}\n\n{suffix}".strip()
    return f"{system_prompt.rstrip()}\n\n{start}\n{memory_block}\n{end}".strip()


def _strip_current_user_turn(full_history: list[ChatHistoryItem], payload: BeforeLlmCallHookPayload) -> list[ChatHistoryItem]:
    if not full_history or payload.turn.user_turn is None:
        return full_history
    tail = full_history[-1]
    if not isinstance(tail, SessionMessageBlock):
        return full_history
    if tail.role.value != "user":
        return full_history
    current_user_text = payload.turn.user_turn.message.as_plain_text().strip()
    if current_user_text and tail.as_plain_text().strip() == current_user_text:
        return full_history[:-1]
    return full_history


def _sanitize_memory_for_prompt(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    dropping_gap = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(LEGACY_MEMORY_CHECKPOINT_PREFIX):
            continue
        if stripped.startswith("## GapRepair"):
            dropping_gap = True
            continue
        if dropping_gap and stripped.startswith("## "):
            dropping_gap = False
        if not dropping_gap:
            out.append(line)
    cleaned = "\n".join(out).strip()
    return cleaned

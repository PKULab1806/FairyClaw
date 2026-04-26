from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fairyclaw.core.agent.hooks.protocol import AfterLlmResponseHookPayload, HookStageInput, HookStageOutput, HookStatus
from fairyclaw.infrastructure.llm.factory import create_llm_client
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter
from fairyclaw_plugins.session_memory.config import SessionMemoryRuntimeConfig

from ._extraction_checkpoint_state import (
    extraction_checkpoint_path,
    load_extraction_checkpoint,
    migrate_legacy_checkpoint_from_memory_md,
    save_extraction_checkpoint,
    strip_legacy_checkpoint_lines,
)
from ._memory_files import append_memory_text, read_memory_text, write_memory_text

logger = logging.getLogger(__name__)
_STATE_NAME = "MEMORY.md"


async def execute_hook(
    hook_input: HookStageInput[AfterLlmResponseHookPayload],
) -> HookStageOutput[AfterLlmResponseHookPayload]:
    payload = hook_input.payload
    cfg = SessionMemoryRuntimeConfig.model_validate(_load_group_config())

    transcript = _build_transcript(payload)
    if not transcript.strip():
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)

    state = _load_checkpoint_state(cfg)
    state["since_messages"] += 1
    state["since_tokens"] += TokenCounter(model="gpt-4").count_text(transcript)
    state["since_tool_rounds"] += len(payload.tool_calls)

    should_trigger = _should_trigger_llm(state=state, cfg=cfg)
    if should_trigger:
        plan = await _run_agentic_search_and_memory_retrieval(transcript=transcript, cfg=cfg)
        _apply_update_plan(plan=plan, cfg=cfg)
        state["cooldown"] = cfg.extract_cooldown_turns
        state["since_messages"] = 0
        state["since_tokens"] = 0
        state["since_tool_rounds"] = 0
    elif state.get("cooldown", 0) > 0:
        state["cooldown"] = max(0, int(state["cooldown"]) - 1)

    _save_checkpoint_state(state=state, cfg=cfg)
    return HookStageOutput(status=HookStatus.OK, patched_payload=payload)


def _load_group_config() -> dict[str, object]:
    from fairyclaw.config.loader import load_yaml
    from pathlib import Path

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        return load_yaml(config_path)
    return {}


def _build_transcript(payload: AfterLlmResponseHookPayload) -> str:
    lines: list[str] = []
    if payload.message_text and payload.message_text.strip():
        lines.append(f"assistant: {payload.message_text.strip()}")
    for call in payload.tool_calls:
        lines.append(f"tool_call: {call.name} args={call.arguments_json}")
    return "\n".join(lines)


def _load_checkpoint_state(cfg: SessionMemoryRuntimeConfig) -> dict[str, int]:
    if extraction_checkpoint_path(memory_root=cfg.memory_root).exists():
        return load_extraction_checkpoint(memory_root=cfg.memory_root)
    memory_text = read_memory_text(name=_STATE_NAME, memory_root=cfg.memory_root)
    migrated = migrate_legacy_checkpoint_from_memory_md(memory_root=cfg.memory_root, memory_text=memory_text)
    if migrated is None:
        return load_extraction_checkpoint(memory_root=cfg.memory_root)
    cleaned = strip_legacy_checkpoint_lines(memory_text)
    new_body = f"{cleaned}\n" if cleaned else ""
    if new_body != memory_text:
        write_memory_text(name=_STATE_NAME, content=new_body, memory_root=cfg.memory_root)
    save_extraction_checkpoint(memory_root=cfg.memory_root, state=migrated)
    return migrated


def _save_checkpoint_state(*, state: dict[str, int], cfg: SessionMemoryRuntimeConfig) -> None:
    save_extraction_checkpoint(memory_root=cfg.memory_root, state=state)


def _should_trigger_llm(*, state: dict[str, int], cfg: SessionMemoryRuntimeConfig) -> bool:
    if int(state.get("cooldown", 0)) > 0:
        return False
    return (
        int(state.get("since_messages", 0)) >= cfg.extract_trigger_message_count
        or int(state.get("since_tokens", 0)) >= cfg.extract_trigger_token_count
        or int(state.get("since_tool_rounds", 0)) >= cfg.extract_trigger_tool_round_count
    )


async def _run_agentic_search_and_memory_retrieval(*, transcript: str, cfg: SessionMemoryRuntimeConfig) -> dict[str, object]:
    user_text = read_memory_text(name="USER.md", memory_root=cfg.memory_root)
    soul_text = read_memory_text(name="SOUL.md", memory_root=cfg.memory_root)
    memory_full = read_memory_text(name="MEMORY.md", memory_root=cfg.memory_root)
    memory_text = strip_legacy_checkpoint_lines(memory_full)[-2000:]
    prompt = (
        "Run Agentic Search and Memory Retrieval over this incremental transcript.\n"
        "Produce a JSON update plan for USER.md, SOUL.md, MEMORY.md.\n"
        "Use conflict-aware updates and keep stable facts in USER, behavior policy in SOUL, episodic details in MEMORY.\n"
        "Schema:\n"
        "{"
        "\"updates\":{\"USER\":{\"append\":[],\"patch\":[]},\"SOUL\":{\"append\":[],\"patch\":[]},\"MEMORY\":{\"append\":[],\"patch\":[]}},"
        "\"conflicts\":[],\"confidence\":{\"USER\":0.0,\"SOUL\":0.0,\"MEMORY\":0.0}"
        "}\n\n"
        f"Current USER.md:\n{user_text}\n\n"
        f"Current SOUL.md:\n{soul_text}\n\n"
        f"Current MEMORY.md tail:\n{memory_text}\n\n"
        f"Incremental transcript:\n{transcript}\n"
    )
    client = create_llm_client(cfg.extraction_profile)
    response = await client.chat(
        messages=[
            {"role": "system", "content": "Return strict JSON only. No markdown fences."},
            {"role": "user", "content": prompt},
        ]
    )
    try:
        data = json.loads(str(response))
    except Exception:
        return {"updates": {"USER": {"append": [], "patch": []}, "SOUL": {"append": [], "patch": []}, "MEMORY": {"append": [], "patch": []}}}
    return data if isinstance(data, dict) else {"updates": {}}


def _apply_update_plan(*, plan: dict[str, object], cfg: SessionMemoryRuntimeConfig) -> None:
    updates = plan.get("updates", {})
    if not isinstance(updates, dict):
        return
    confidence = plan.get("confidence", {})
    user_conf = float(confidence.get("USER", 0.0)) if isinstance(confidence, dict) else 0.0
    for name in ("USER", "SOUL", "MEMORY"):
        item = updates.get(name, {})
        if not isinstance(item, dict):
            continue
        if name == "USER" and user_conf < cfg.min_confidence_to_write_user:
            continue
        append_items = item.get("append", [])
        if isinstance(append_items, list):
            for row in append_items:
                text = str(row).strip()
                if not text:
                    continue
                append_memory_text(name=f"{name}.md", content=f"- {text}", memory_root=cfg.memory_root)



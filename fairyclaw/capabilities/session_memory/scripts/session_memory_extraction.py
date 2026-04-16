from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fairyclaw.core.agent.hooks.protocol import AfterLlmResponseHookPayload, HookStageInput, HookStageOutput, HookStatus
from fairyclaw.infrastructure.llm.factory import create_llm_client
from fairyclaw.infrastructure.tokenizer.counter import TokenCounter
from fairyclaw_plugins.session_memory.config import SessionMemoryRuntimeConfig

from ._memory_files import append_memory_text, read_memory_text, write_memory_text

logger = logging.getLogger(__name__)
_STATE_NAME = "MEMORY.md"
_CHECKPOINT_MARKER = "<!-- session_memory_checkpoint -->"


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

    heuristic_entries = _heuristic_extract(transcript)
    for line in heuristic_entries:
        append_memory_text(name="MEMORY.md", content=line, memory_root=cfg.memory_root)

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


def _heuristic_extract(transcript: str) -> list[str]:
    out: list[str] = []
    if "http://" in transcript or "https://" in transcript:
        out.append(f"- discovered_url: {transcript[:240]}")
    if "/" in transcript:
        out.append(f"- discovered_path_or_marker: {transcript[:240]}")
    return out


def _load_checkpoint_state(cfg: SessionMemoryRuntimeConfig) -> dict[str, int]:
    content = read_memory_text(name=_STATE_NAME, memory_root=cfg.memory_root)
    line = ""
    for row in reversed(content.splitlines()):
        if row.strip().startswith(_CHECKPOINT_MARKER):
            line = row.strip()
            break
    if not line:
        return {"since_messages": 0, "since_tokens": 0, "since_tool_rounds": 0, "cooldown": 0}
    raw = line.replace(_CHECKPOINT_MARKER, "", 1).strip()
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return {
        "since_messages": int(data.get("since_messages", 0)),
        "since_tokens": int(data.get("since_tokens", 0)),
        "since_tool_rounds": int(data.get("since_tool_rounds", 0)),
        "cooldown": int(data.get("cooldown", 0)),
    }


def _save_checkpoint_state(*, state: dict[str, int], cfg: SessionMemoryRuntimeConfig) -> None:
    marker = f"{_CHECKPOINT_MARKER} {json.dumps(state, ensure_ascii=False)}"
    content = read_memory_text(name=_STATE_NAME, memory_root=cfg.memory_root)
    lines = [line for line in content.splitlines() if not line.strip().startswith(_CHECKPOINT_MARKER)]
    rebuilt = "\n".join(lines).rstrip()
    if rebuilt:
        rebuilt = f"{rebuilt}\n{marker}\n"
    else:
        rebuilt = f"{marker}\n"
    write_memory_text(name=_STATE_NAME, content=rebuilt, memory_root=cfg.memory_root)


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
    memory_text = read_memory_text(name="MEMORY.md", memory_root=cfg.memory_root)[-2000:]
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



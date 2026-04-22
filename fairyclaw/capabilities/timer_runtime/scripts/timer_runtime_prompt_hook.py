from __future__ import annotations

from dataclasses import replace

from fairyclaw.config.settings import settings
from fairyclaw.core.agent.context.system_prompts import normalize_prompt_language
from fairyclaw.sdk.hooks import (
    BeforeLlmCallHookPayload,
    HookStageInput,
    HookStageOutput,
    HookStatus,
)


_TIMER_GUIDE_BLOCK_EN = (
    "[TimerRuntimeGuide]\n"
    "- Use heartbeat for short-lived polling/monitoring tasks.\n"
    "- Use cron only for schedule-driven tasks; cron must be 5 fields: minute hour day month weekday.\n"
    "- Create timer jobs only via create_timer_job; when mode=cron, pass cron_expr directly.\n"
    "- If create_timer_job returns a validation error, fix arguments and retry once.\n"
    "- System timezone is used by default; do not require explicit timezone.\n"
    "[/TimerRuntimeGuide]"
)

_TIMER_GUIDE_BLOCK_ZH = (
    "[TimerRuntimeGuide]\n"
    "- 短期轮询/监控任务优先使用 heartbeat。\n"
    "- 仅在固定时点调度需求下使用 cron；cron 必须为 5 段：分 时 日 月 周。\n"
    "- 创建定时任务只能通过 create_timer_job；当 mode=cron 时直接传 cron_expr。\n"
    "- 如果 create_timer_job 返回校验错误，请根据错误修正参数后重试一次。\n"
    "- 默认使用系统时区解释 cron，无需显式 timezone 参数。\n"
    "[/TimerRuntimeGuide]"
)


async def execute_hook(
    hook_input: HookStageInput[BeforeLlmCallHookPayload],
) -> HookStageOutput[BeforeLlmCallHookPayload]:
    payload = hook_input.payload
    turn = payload.turn
    if not turn.llm_messages:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    first = turn.llm_messages[0]
    if first.role != "system":
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    content = str(first.content or "")
    if "[TimerRuntimeGuide]" in content:
        return HookStageOutput(status=HookStatus.SKIP, patched_payload=payload)
    guide_block = _TIMER_GUIDE_BLOCK_ZH if normalize_prompt_language(settings.system_prompt_language) == "zh" else _TIMER_GUIDE_BLOCK_EN
    patched_messages = list(turn.llm_messages)
    patched_messages[0] = replace(first, content=(content.rstrip() + "\n\n" + guide_block).strip())
    patched_payload = replace(payload, turn=replace(turn, llm_messages=patched_messages))
    return HookStageOutput(status=HookStatus.OK, patched_payload=patched_payload)

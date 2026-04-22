from __future__ import annotations

import json
from typing import Any

from fairyclaw.sdk.timers import create_timer_job
from fairyclaw.sdk.tools import ToolContext


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    mode = str(args.get("mode") or "").strip().lower()
    payload_text = args.get("payload")
    if payload_text is None:
        payload_text = ""
    elif not isinstance(payload_text, str):
        payload_text = str(payload_text)
    record, error = await create_timer_job(
        creator_session_id=context.session_id,
        mode=mode,
        payload=str(payload_text),
        cron_expr=(str(args.get("cron_expr") or "").strip() or None),
        interval_seconds=(int(args["interval_seconds"]) if args.get("interval_seconds") is not None else None),
        start_delay_seconds=(int(args["start_delay_seconds"]) if args.get("start_delay_seconds") is not None else None),
        deadline_seconds=(int(args["deadline_seconds"]) if args.get("deadline_seconds") is not None else None),
        max_runs=(int(args["max_runs"]) if args.get("max_runs") is not None else None),
    )
    if record is None:
        return json.dumps({"status": "error", "error": error or "failed to create timer job"}, ensure_ascii=False)
    return json.dumps({"status": "ok", "job": record}, ensure_ascii=False)

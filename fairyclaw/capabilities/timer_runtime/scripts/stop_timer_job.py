from __future__ import annotations

import json
from typing import Any

from fairyclaw.sdk.timers import get_timer_job, stop_timer_job
from fairyclaw.sdk.tools import ToolContext

from ._shared import owner_and_creator_scope


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return json.dumps({"status": "error", "error": "job_id is required"}, ensure_ascii=False)

    current = await get_timer_job(job_id)
    if current is None:
        return json.dumps({"status": "error", "error": "job not found"}, ensure_ascii=False)

    owner_session_id, creator_filter = await owner_and_creator_scope(context.session_id)
    if current.get("owner_session_id") != owner_session_id:
        return json.dumps({"status": "error", "error": "access denied for this job"}, ensure_ascii=False)
    if creator_filter is not None and current.get("creator_session_id") != creator_filter:
        return json.dumps({"status": "error", "error": "sub-session can only stop jobs it created"}, ensure_ascii=False)

    row = await stop_timer_job(job_id)
    if row is None:
        return json.dumps({"status": "error", "error": "failed to stop job"}, ensure_ascii=False)
    return json.dumps({"status": "ok", "job": row}, ensure_ascii=False)

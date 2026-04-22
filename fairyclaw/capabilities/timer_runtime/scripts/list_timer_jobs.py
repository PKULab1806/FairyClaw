from __future__ import annotations

import json
from typing import Any

from fairyclaw.sdk.timers import list_timer_jobs
from fairyclaw.sdk.tools import ToolContext

from ._shared import is_sub_session, owner_and_creator_scope


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    statuses_raw = args.get("statuses")
    statuses = [str(s).strip() for s in statuses_raw if isinstance(s, str) and str(s).strip()] if isinstance(statuses_raw, list) else None
    limit = int(args.get("limit") or 50)
    limit = max(1, min(limit, 200))
    owner_session_id, creator_filter = await owner_and_creator_scope(context.session_id)
    include_all_owner_jobs = bool(args.get("include_all_owner_jobs"))
    if is_sub_session(context.session_id):
        include_all_owner_jobs = False

    rows = await list_timer_jobs(
        owner_session_id=owner_session_id,
        creator_session_id=(None if include_all_owner_jobs else creator_filter),
        statuses=statuses,
        limit=limit,
    )
    return json.dumps({"status": "ok", "jobs": rows}, ensure_ascii=False)

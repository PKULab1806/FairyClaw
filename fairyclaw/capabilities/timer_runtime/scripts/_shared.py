from __future__ import annotations

from fairyclaw.sdk.timers import resolve_owner_session_id
from fairyclaw.sdk.types import SUB_SESSION_MARKER


def is_sub_session(session_id: str) -> bool:
    return SUB_SESSION_MARKER in str(session_id or "")


async def owner_and_creator_scope(session_id: str) -> tuple[str, str | None]:
    owner_session_id = await resolve_owner_session_id(session_id)
    if is_sub_session(session_id):
        return owner_session_id, session_id
    return owner_session_id, None

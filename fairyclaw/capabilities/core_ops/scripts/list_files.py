# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from typing import Any, Dict
from fairyclaw.core.capabilities.models import ToolContext, SessionFileListItem, get_context_db
from fairyclaw.infrastructure.database.repository import FileRepository
import json

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """List all files bound to current session in database.

    Args:
        args (Dict[str, Any]): Tool arguments (unused in current implementation).
        context (ToolContext): Tool runtime context containing session_id and memory adapter.

    Returns:
        str: JSON array of session file summaries.

    Key Logic:
        - Retrieves request-scoped database session from tool context.
        - Queries file records by current session_id.
        - Serializes records into stable JSON payload.

    Errors:
        - Returns memory-access error when DB handle is unavailable in context.
    """
    session_id = context.session_id
    db, error = get_context_db(context)
    if error:
        return error

    repo = FileRepository(db)
    files = await repo.list_by_session(session_id)
    
    file_list: list[dict[str, Any]] = []
    for f in files:
        file_list.append(
            SessionFileListItem(
                file_id=f.id,
                filename=f.filename,
                size=f.size,
                mime_type=f.mime_type or "",
            ).to_dict()
        )
    
    return json.dumps(file_list, ensure_ascii=False)

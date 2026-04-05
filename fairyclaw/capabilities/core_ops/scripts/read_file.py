# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from typing import Any, Dict
from fairyclaw.infrastructure.database.repository import FileRepository
from fairyclaw.sdk.tools import ToolContext, get_context_db

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Read text content of a session-scoped file by file ID.

    Args:
        args (Dict[str, Any]): Tool arguments containing `file_id` (str).
        context (ToolContext): Tool runtime context containing session_id and memory adapter.

    Returns:
        str: UTF-8 decoded file content or standardized error message.

    Key Logic:
        - Validates target file_id.
        - Restricts lookup to current session for tenant isolation.
        - Decodes binary payload as UTF-8 for text-only reading.

    Errors:
        - Returns error when `file_id` is missing.
        - Returns error when file does not exist in current session.
        - Returns error when file is binary and not UTF-8 decodable.
    """
    file_id = args.get("file_id")
    if not file_id:
        return "Error: file_id is required."
        
    session_id = context.session_id
    db, error = get_context_db(context)
    if error:
        return error

    repo = FileRepository(db)
    file_model = await repo.get_for_session(file_id=file_id, session_id=session_id)
    if not file_model:
        return f"Error: File {file_id} not found."
    
    content = file_model.content
    if not content:
        return ""
    
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return f"Error: File content is binary (mime: {file_model.mime_type}). Cannot read as text."

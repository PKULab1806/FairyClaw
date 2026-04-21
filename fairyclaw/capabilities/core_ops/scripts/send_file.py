# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
import mimetypes
from typing import Any, Dict
from fairyclaw.sdk.runtime import deliver_file_to_user
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path
from fairyclaw.infrastructure.database.session import get_db_session
from fairyclaw.infrastructure.database.repository import FileRepository


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Bind one local file to the session and deliver it to the user channel.

    Args:
        args (Dict[str, Any]): Tool arguments containing `file_path` (str).
        context (ToolContext): Tool runtime context containing session identifier.

    Returns:
        str: Human-readable delivery result or error message.

    Key Logic:
        - Validates file path under allowed root.
        - Persists file into the session file table before delivery.
        - Calls the globally registered file delivery implementation directly.

    Errors:
        - Returns error when file_path is missing or inaccessible.
        - Returns error when DB binding fails.
        - Returns error when delivery invocation fails.
    """
    file_path = args.get("file_path")
    if not file_path:
        return "Error: file_path is required."

    root_dir = context.filesystem_root_dir
    safe_path, error = resolve_safe_path(file_path, root_dir, context.workspace_root)
    if error or safe_path is None:
        return error or "Error: Invalid path."
    abs_path = safe_path.path

    if not os.path.exists(abs_path):
        return f"Error: File not found at {abs_path}"

    try:
        with open(abs_path, "rb") as f:
            content = f.read()

        filename = os.path.basename(abs_path)
        mime_type, _ = mimetypes.guess_type(abs_path)

        async for db in get_db_session():
            file_repo = FileRepository(db)
            file_model = await file_repo.create(
                session_id=context.session_id,
                filename=filename,
                content=content,
                mime_type=mime_type
            )
            file_id = file_model.id
            break
    except Exception as e:
        return f"Error binding file to session: {str(e)}"
    await deliver_file_to_user(context.session_id, file_id)
    return f"File sent (file_id={file_id})."

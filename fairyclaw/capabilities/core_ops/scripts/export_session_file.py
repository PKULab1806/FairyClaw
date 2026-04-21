# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
from typing import Any, Dict

from fairyclaw.sdk.tools import ToolContext, get_context_db, resolve_safe_path
from fairyclaw.infrastructure.database.repository import FileRepository

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Export session file from database to local filesystem path.

    Args:
        args (Dict[str, Any]): Tool arguments containing `file_id` and optional `target_path`.
        context (ToolContext): Tool runtime context with session information.

    Returns:
        str: Success message with destination path, or standardized error message.

    Key Logic:
        - Restricts source file lookup to current session.
        - Resolves destination as directory-append or explicit file path.
        - Creates destination parent directories before binary write.

    Errors:
        - Returns error when file_id is missing or not found in session.
        - Returns error when filesystem write or database access fails.
    """
    file_id = args.get("file_id")
    target_path = args.get("target_path")
    
    if not file_id:
        return "Error: file_id is required."
        
    if not target_path:
        target_path = context.workspace_root or os.getcwd()
    root_dir = context.filesystem_root_dir

    db, error = get_context_db(context)
    if error:
        return error
        
    try:
        repo = FileRepository(db)
        file_model = await repo.get_for_session(file_id=file_id, session_id=context.session_id)
        
        if not file_model:
            return f"Error: File with ID '{file_id}' not found in the current session."
            
        safe_target, safe_error = resolve_safe_path(target_path, root_dir, context.workspace_root)
        if safe_error or safe_target is None:
            return safe_error or "Error: Invalid target path."
        safe_target_path = safe_target.path

        # Determine the full destination path
        if os.path.isdir(safe_target_path):
            # If target is a directory, append the original filename
            dest_path = os.path.join(safe_target_path, file_model.filename)
        else:
            # Otherwise, use target_path as the full file path
            dest_path = safe_target_path

        safe_dest, dest_error = resolve_safe_path(dest_path, root_dir, context.workspace_root)
        if dest_error or safe_dest is None:
            return dest_error or "Error: Invalid export destination path."
        dest_path = safe_dest.path
            
        # Ensure the parent directory exists
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        
        # Write the binary content to disk
        with open(dest_path, "wb") as f:
            f.write(file_model.content)
            
        return f"Success: File '{file_model.filename}' (ID: {file_id}) has been exported to '{dest_path}'."
        
    except Exception as e:
        return f"Error exporting file: {str(e)}"

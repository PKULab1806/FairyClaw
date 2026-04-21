# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
from typing import Any, Dict
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Delete file or empty directory from local filesystem under allowed root.

    Args:
        args (Dict[str, Any]): Tool arguments containing `file_path` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Success message or standardized error message.

    Key Logic:
        - Resolves target path with filesystem root boundary checks.
        - Verifies target existence.
        - Deletes file directly or removes empty directory.

    Errors:
        - Returns error when required argument is missing.
        - Returns error when path is invalid or outside allowed root.
        - Returns error when filesystem deletion fails.
    """
    file_path = args.get("file_path")
    if not file_path:
        return "Error: file_path is required."
        
    # 1. Resolve Path
    root_dir = context.filesystem_root_dir
    safe_path, error = resolve_safe_path(file_path, root_dir, context.workspace_root)
    if error or safe_path is None:
        return error or "Error: Invalid path."
    abs_path = safe_path.path
        
    if not os.path.exists(abs_path):
        return f"Error: File not found at {abs_path}"
        
    try:
        if os.path.isdir(abs_path):
            os.rmdir(abs_path) # Only empty dirs
            return f"Directory removed successfully from {abs_path}"
        else:
            os.remove(abs_path)
            return f"File removed successfully from {abs_path}"
    except Exception as e:
        return f"Error deleting file: {str(e)}"

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
from typing import Any, Dict
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Write UTF-8 text content to local filesystem under allowed root.

    Args:
        args (Dict[str, Any]): Tool arguments containing `file_path` and `content`.
        context (ToolContext): Tool runtime context.

    Returns:
        str: Success confirmation or standardized error message.

    Key Logic:
        - Validates and normalizes path against configured filesystem root.
        - Creates parent directory structure when absent.
        - Writes content using UTF-8 encoding.

    Errors:
        - Returns error when required arguments are missing.
        - Returns error when resolved path is invalid/outside allowed root.
        - Returns error when filesystem write fails.
    """
    file_path = args.get("file_path")
    content = args.get("content")
    
    if not file_path:
        return "Error: file_path is required."
    if content is None:
        return "Error: content is required."
        
    # 1. Resolve Path
    root_dir = context.filesystem_root_dir
    safe_path, error = resolve_safe_path(file_path, root_dir, context.workspace_root)
    if error or safe_path is None:
        return error or "Error: Invalid path."
    abs_path = safe_path.path
        
    try:
        # Create directories if needed
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"File written successfully to {abs_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

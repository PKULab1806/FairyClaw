# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
from typing import Any, Dict
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Read UTF-8 text content from a local file under allowed root.

    Args:
        args (Dict[str, Any]): Tool arguments containing `file_path` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: File content on success, otherwise standardized error message.

    Key Logic:
        - Validates and normalizes target path using filesystem root guard.
        - Verifies target exists and is a regular file.
        - Reads file as UTF-8 text and rejects binary payloads.

    Errors:
        - Returns error when `file_path` is missing.
        - Returns error when path is out of allowed root.
        - Returns error when file does not exist or is not text-readable.
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
        
    if not os.path.isfile(abs_path):
        return f"Error: {abs_path} is not a file."
        
    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except UnicodeDecodeError:
        return "Error: File is binary and cannot be read as text."
    except Exception as e:
        return f"Error reading file: {str(e)}"

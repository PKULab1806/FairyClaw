# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
import json
from typing import Any, Dict
from fairyclaw.sdk.tools import ToolContext, FileSystemListItem, resolve_safe_path

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """List directory entries under allowed filesystem root.

    Args:
        args (Dict[str, Any]): Tool arguments optionally containing `dir_path` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: JSON array string of file/directory summaries or error message.

    Key Logic:
        - Uses configured root as default when `dir_path` is absent.
        - Validates and normalizes path inside allowed root boundary.
        - Builds structured result with name/type/size/path fields.

    Errors:
        - Returns error when path is invalid or outside allowed root.
        - Returns error when target does not exist or is not a directory.
    """
    dir_path = args.get("dir_path")
    
    # 1. Resolve Path
    root_dir = context.filesystem_root_dir
    resolved_dir_path = str(dir_path) if isinstance(dir_path, str) and dir_path else str(context.workspace_root or root_dir or "")

    safe_path, error = resolve_safe_path(resolved_dir_path, root_dir, context.workspace_root)
    if error or safe_path is None:
        return error or "Error: Invalid path."
    abs_path = safe_path.path
        
    if not os.path.exists(abs_path):
        return f"Error: Directory not found at {abs_path}"
        
    if not os.path.isdir(abs_path):
        return f"Error: {abs_path} is not a directory."
        
    try:
        items = os.listdir(abs_path)
        file_list: list[dict[str, Any]] = []
        for item in items:
            item_path = os.path.join(abs_path, item)
            is_dir = os.path.isdir(item_path)
            size = os.path.getsize(item_path) if not is_dir else 0
            list_item = FileSystemListItem(
                name=item,
                item_type="directory" if is_dir else "file",
                size=size,
                path=item_path,
            )
            file_list.append(list_item.to_dict())
        return json.dumps(file_list, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing directory: {str(e)}"

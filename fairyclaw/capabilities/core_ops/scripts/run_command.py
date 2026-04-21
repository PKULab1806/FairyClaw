# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import os
from typing import Any, Dict
from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.subtasks import is_sub_session_cancel_requested
from fairyclaw.sdk.tools import ToolContext
from fairyclaw_plugins.core_ops.config import CoreOpsRuntimeConfig

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Execute shell command with timeout and captured output streams.

    Args:
        args (Dict[str, Any]): Tool arguments containing `command` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Combined execution report including exit code/stdout/stderr.

    Key Logic:
        - Uses configured timeout to bound command runtime.
        - Captures stdout and stderr asynchronously.
        - Returns normalized textual report for model consumption.

    Errors:
        - Returns error when command argument is missing.
        - Returns timeout error and kills process when exceeded.
        - Returns execution error when subprocess creation fails.
    """
    command = args.get("command")
    cfg = expect_group_config(context, CoreOpsRuntimeConfig)
    timeout = cfg.execution_timeout_seconds

    if not command:
        return "Error: command is required."

    try:
        # Create subprocess
        # We don't set cwd explicitly, so it inherits from the parent process
        # The parent process (the server) has already chdir'd to FAIRYCLAW_FILESYSTEM_ROOT_DIR on startup
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=(context.workspace_root or context.filesystem_root_dir or os.getcwd()),
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        while True:
            if is_sub_session_cancel_requested(context.session_id):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
                return "Error: Command cancelled because sub-task was killed."
            if process.returncode is not None:
                break
            if loop.time() >= deadline:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
                return f"Error: Command timed out after {timeout} seconds."
            await asyncio.sleep(0.2)

        stdout, stderr = await process.communicate()

        output = stdout.decode('utf-8', errors='replace').strip()
        error = stderr.decode('utf-8', errors='replace').strip()
        exit_code = process.returncode

        result_parts = []
        if exit_code != 0:
            result_parts.append(f"Exit Code: {exit_code}")
        
        if output:
            result_parts.append(f"Stdout:\n{output}")
        if error:
            result_parts.append(f"Stderr:\n{error}")
            
        if not result_parts:
            return "Command executed successfully with no output."
            
        return "\n\n".join(result_parts)

    except Exception as e:
        return f"Error executing command: {str(e)}"

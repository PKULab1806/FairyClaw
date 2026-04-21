# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
from dataclasses import dataclass
import os
from typing import Any, Dict
from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.subtasks import is_sub_session_cancel_requested
from fairyclaw.sdk.tools import ToolContext
from fairyclaw_plugins.core_ops.config import CoreOpsRuntimeConfig

@dataclass
class RunResult:
    """Represent subprocess execution outcome for local Python runner."""
    stdout: str
    stderr: str
    exit_code: int

class LocalPythonExecutor:
    """Execute ad-hoc Python snippets in local subprocess."""

    async def execute(self, session_id: str, code: str, timeout: int = 30, cwd: str | None = None) -> RunResult:
        """Execute Python code using `python3 -c` and capture outputs.

        Args:
            session_id (str): Session identifier reserved for future isolation policies.
            code (str): Python source code to execute.

        Returns:
            RunResult: Exit code plus captured stdout/stderr.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=(cwd or os.getcwd()),
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + float(timeout)
            while True:
                if is_sub_session_cancel_requested(session_id):
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
                    return RunResult(
                        stdout="",
                        stderr="Execution cancelled because sub-task was killed.",
                        exit_code=1,
                    )
                if process.returncode is not None:
                    break
                if loop.time() >= deadline:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
                    return RunResult(
                        stdout="",
                        stderr=f"Execution timed out after {timeout} seconds.",
                        exit_code=1,
                    )
                await asyncio.sleep(0.2)
            stdout, stderr = await process.communicate()
            exit_code = process.returncode if process.returncode is not None else 1
            return RunResult(
                stdout=stdout.decode().strip(),
                stderr=stderr.decode().strip(),
                exit_code=exit_code
            )
        except Exception as e:
            return RunResult(stdout="", stderr=str(e), exit_code=1)

local_python_executor = LocalPythonExecutor()

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Tool entrypoint for local Python execution.

    Args:
        args (Dict[str, Any]): Tool arguments containing `code` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Structured execution report with exit code/stdout/stderr.

    Errors:
        - Returns error when `code` is missing.
        - Subprocess runtime exceptions are converted to stderr in returned report.
    """
    code = args.get("code")
    if not code:
        return "Error: code is required."
        
    cfg = expect_group_config(context, CoreOpsRuntimeConfig)
    result = await local_python_executor.execute(
        context.session_id,
        code,
        timeout=cfg.execution_timeout_seconds,
        cwd=(context.workspace_root or context.filesystem_root_dir),
    )
    output = f"Exit Code: {result.exit_code}\nStdout:\n{result.stdout}"
    if result.stderr:
        output += f"\nStderr:\n{result.stderr}"
    return output

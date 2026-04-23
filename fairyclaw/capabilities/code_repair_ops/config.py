# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime configuration for CodeRepairOps capability group."""

from pydantic import BaseModel, Field


class CodeRepairOpsRuntimeConfig(BaseModel):
    """Frozen runtime configuration for CodeRepairOps tools."""

    model_config = {"frozen": True}

    max_changed_lines: int = 60
    # Guardrail for tests/fixtures in benchmark-style tasks.
    protected_file_globs: list[str] = Field(
        default_factory=lambda: [
            "**/test_*.py",
            "**/*_test.py",
            "**/fixtures/**/test_*.py",
        ]
    )
    # Allow only common non-destructive command prefixes in verification checks.
    command_allow_prefixes: list[str] = Field(
        default_factory=lambda: [
            "pytest",
            "python",
            "python3",
            "test ",
            "git status",
            "git diff",
            "git log",
            "git merge-base",
            "git rev-parse",
            "sed -n",
            "rg",
            "ls",
            "cat",
            "pwd",
        ]
    )
    # Evidence collection must be read/test oriented (no mutating shell).
    evidence_command_allow_prefixes: list[str] = Field(
        default_factory=lambda: [
            "pytest",
            "python",
            "python3",
            "git status",
            "git diff",
            "git log",
            "git merge-base",
            "git rev-parse",
            "rg",
            "ls",
            "cat",
            "sed -n",
            "pwd",
        ]
    )
runtime_config_model = CodeRepairOpsRuntimeConfig


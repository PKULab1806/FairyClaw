# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.core.agent.context import system_prompts


def test_planner_prompt_includes_memory_tool_policy() -> None:
    prompt = system_prompts.build_system_prompt(nesting_depth=0, task_type="general", prompt_language="en")
    assert "read_memory_file" in prompt
    assert "write_memory_file" in prompt
    assert "append_memory_file" in prompt
    assert "do not delegate memory writes" in prompt.lower()


def test_prompt_language_switches_to_chinese() -> None:
    prompt = system_prompts.build_system_prompt(nesting_depth=0, task_type="general", prompt_language="zh")
    assert "你是 FairyClaw" in prompt
    assert "[RoleIdentity]" in prompt


def test_planner_prompt_guides_memory_file_selection() -> None:
    prompt = system_prompts.build_system_prompt(nesting_depth=0, task_type="general", prompt_language="en")
    assert "Write/append `USER.md` when stable user profile facts appear" in prompt
    assert "Write/append `SOUL.md` when enduring assistant behavior principles are clarified" in prompt
    assert "Append `MEMORY.md` after important decisions, tool failures, unresolved TODOs" in prompt
    assert "Read `USER.md` before first personalized reply" not in prompt


def test_sub_agent_prompt_adds_filesystem_constraint(monkeypatch) -> None:
    monkeypatch.setattr(system_prompts.settings, "filesystem_root_dir", "/tmp/workspace")
    prompt = system_prompts.build_system_prompt(nesting_depth=1, task_type="code", prompt_language="en")
    assert "[FilesystemConstraint]" in prompt
    assert "FAIRYCLAW_FILESYSTEM_ROOT_DIR=/tmp/workspace" in prompt


def test_prompt_adds_workspace_constraint_when_workspace_present() -> None:
    prompt = system_prompts.build_system_prompt(
        nesting_depth=0,
        task_type="general",
        prompt_language="en",
        workspace_root="/tmp/my-ws",
    )
    assert "[WorkspaceConstraint]" in prompt
    assert "workspace_root=/tmp/my-ws" in prompt

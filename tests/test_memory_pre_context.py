# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.capabilities.memory_hooks.scripts.memory_pre_context import _sanitize_compaction_summary


def test_sanitize_compaction_summary_removes_think_artifacts_and_preamble() -> None:
    raw = """
    </think>
    <think>I need to summarize the conversation.</think>
    </think>
    Drafting notes before final answer.

    User intent:
    - Remember marker A-B-C

    Changes made:
    - Stored marker
    """
    cleaned = _sanitize_compaction_summary(raw)
    assert cleaned.startswith("User intent:")
    assert "<think>" not in cleaned
    assert "</think>" not in cleaned
    assert "Drafting notes before final answer." not in cleaned


def test_sanitize_compaction_summary_keeps_clean_structured_summary() -> None:
    raw = (
        "User intent:\n- Remember path\n\n"
        "Changes made:\n- Stored path\n\n"
        "Key decisions:\n- Keep concise\n\n"
        "Next steps:\n- None"
    )
    assert _sanitize_compaction_summary(raw) == raw

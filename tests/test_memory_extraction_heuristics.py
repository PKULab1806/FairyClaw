# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.capabilities.memory_hooks.scripts.memory_extraction import _extract_user_facts


def test_extract_user_facts_includes_marker_path_and_url() -> None:
    facts = _extract_user_facts(
        "Remember marker BETA-ALPHA-92741, project path /home/zhangxi/projects/gamma, and docs https://example.com/gamma-docs."
    )
    texts = [fact["text"] for fact in facts]
    assert any("BETA-ALPHA-92741" in text for text in texts)
    assert any("/home/zhangxi/projects/gamma" in text for text in texts)
    assert any("https://example.com/gamma-docs" in text for text in texts)


def test_extract_user_facts_skips_plain_noise_without_memory_signals() -> None:
    facts = _extract_user_facts("Noise turn with filler " + ("noiseblock " * 300))
    assert facts == []

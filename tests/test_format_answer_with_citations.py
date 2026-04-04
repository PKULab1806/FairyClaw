# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Unit tests for format_answer_with_citations (no network required)."""

import asyncio
import json

from fairyclaw.capabilities.sourced_research.scripts.format_answer_with_citations import execute
from fairyclaw.core.capabilities.models import ToolContext


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", memory=None)


def run(coro):
    return asyncio.run(coro)


# --- Happy path ---

def test_formats_answer_with_single_citation():
    result = run(execute(
        {
            "answer": "The sky is blue.",
            "citations": [{"url": "https://example.com/sky", "excerpt": "Sky appears blue due to Rayleigh scattering.", "title": "Why is the sky blue?"}],
        },
        _ctx(),
    ))
    assert "The sky is blue." in result
    assert "https://example.com/sky" in result
    assert "Rayleigh scattering" in result
    assert "## Sources" in result


def test_formats_answer_with_multiple_citations():
    result = run(execute(
        {
            "answer": "Multiple facts here.",
            "citations": [
                {"url": "https://a.com", "excerpt": "Fact A."},
                {"url": "https://b.com", "excerpt": "Fact B.", "title": "Source B"},
            ],
        },
        _ctx(),
    ))
    assert "https://a.com" in result
    assert "https://b.com" in result
    assert "Fact A." in result
    assert "Fact B." in result


def test_citations_as_json_string():
    citations = json.dumps([{"url": "https://x.com", "excerpt": "Some text."}])
    result = run(execute({"answer": "Answer.", "citations": citations}, _ctx()))
    assert "https://x.com" in result
    assert "Some text." in result


# --- Validation failures ---

def test_empty_answer_returns_error():
    result = run(execute({"answer": "", "citations": [{"url": "https://x.com", "excerpt": "e"}]}, _ctx()))
    assert result.startswith("Error:")


def test_missing_answer_returns_error():
    result = run(execute({"citations": [{"url": "https://x.com", "excerpt": "e"}]}, _ctx()))
    assert result.startswith("Error:")


def test_empty_citations_list_returns_error():
    result = run(execute({"answer": "OK", "citations": []}, _ctx()))
    assert "Citation validation failed" in result
    assert "at least one entry" in result


def test_citation_missing_url_returns_error():
    result = run(execute({"answer": "OK", "citations": [{"url": "", "excerpt": "some text"}]}, _ctx()))
    assert "Citation validation failed" in result
    assert "'url'" in result


def test_citation_missing_excerpt_returns_error():
    result = run(execute({"answer": "OK", "citations": [{"url": "https://x.com", "excerpt": ""}]}, _ctx()))
    assert "Citation validation failed" in result
    assert "'excerpt'" in result


def test_citations_not_a_list_returns_error():
    result = run(execute({"answer": "OK", "citations": {"url": "https://x.com", "excerpt": "e"}}, _ctx()))
    assert "Citation validation failed" in result


def test_invalid_json_string_citations_returns_error():
    result = run(execute({"answer": "OK", "citations": "{not valid json"}, _ctx()))
    assert result.startswith("Error:")


def test_citations_as_json_string_double_serialized():
    """Model sometimes serializes citations as a JSON string instead of a native array."""
    import json as _json
    citations_str = _json.dumps([{"url": "https://example.com", "excerpt": "Some text."}])
    result = run(execute({"answer": "Answer.", "citations": citations_str}, _ctx()))
    assert "https://example.com" in result
    assert "Some text." in result


def test_citations_as_python_literal_string():
    """Fallback: ast.literal_eval handles single-quoted Python-style lists from some models."""
    citations_str = "[{'url': 'https://example.com', 'excerpt': 'Some text.'}]"
    result = run(execute({"answer": "Answer.", "citations": citations_str}, _ctx()))
    assert "https://example.com" in result
    assert "Some text." in result


def test_missing_citations_returns_error():
    result = run(execute({"answer": "OK"}, _ctx()))
    assert result.startswith("Error:")

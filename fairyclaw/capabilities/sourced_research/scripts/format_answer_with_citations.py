# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Step 3 of the sourced-research pipeline: validate citations and render the final answer."""

import ast
import json
from typing import Any, Dict, List

from fairyclaw.core.capabilities.models import ToolContext


def _validate_citations(citations: List[Any]) -> List[str]:
    """Return a list of validation error strings (empty means valid)."""
    errors: List[str] = []
    if not isinstance(citations, list):
        errors.append("'citations' must be a JSON array.")
        return errors
    if len(citations) == 0:
        errors.append("'citations' must contain at least one entry.")
        return errors
    for i, c in enumerate(citations, 1):
        if not isinstance(c, dict):
            errors.append(f"Citation [{i}]: must be an object with 'url' and 'excerpt' fields.")
            continue
        if not c.get("url", "").strip():
            errors.append(f"Citation [{i}]: 'url' must be a non-empty string.")
        if not c.get("excerpt", "").strip():
            errors.append(f"Citation [{i}]: 'excerpt' must be a non-empty string (use verbatim text from extract_evidence_excerpt).")
    return errors


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Validate citations and return a formatted Markdown answer with a Sources section.

    Args:
        args (Dict[str, Any]): Tool arguments: ``answer`` (str) and ``citations`` (list).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Formatted Markdown answer followed by a Sources section, or a validation error message.
    """
    answer = args.get("answer", "").strip()
    if not answer:
        return "Error: 'answer' must be a non-empty string."

    raw_citations = args.get("citations")
    if raw_citations is None:
        return "Error: 'citations' is required."

    if isinstance(raw_citations, str):
        parsed = None
        # Try standard JSON first
        try:
            parsed = json.loads(raw_citations)
        except json.JSONDecodeError:
            pass
        # Fallback: Python literal syntax (e.g. single-quoted dicts from some models)
        if parsed is None:
            try:
                parsed = ast.literal_eval(raw_citations)
            except (ValueError, SyntaxError):
                pass
        if parsed is None:
            return (
                "Error: 'citations' must be a JSON array of objects, e.g. "
                '[{"url": "https://...", "excerpt": "..."}]. '
                "Do NOT pass it as a serialized string — pass a native array directly."
            )
        raw_citations = parsed

    errors = _validate_citations(raw_citations)
    if errors:
        return "Citation validation failed:\n" + "\n".join(f"- {e}" for e in errors)

    sources_lines = ["## Sources\n"]
    for i, c in enumerate(raw_citations, 1):
        title = c.get("title", "").strip() or c["url"]
        excerpt = c["excerpt"].strip()
        sources_lines.append(f"{i}. **[{title}]({c['url']})**")
        sources_lines.append(f"   > {excerpt}\n")

    return f"{answer}\n\n" + "\n".join(sources_lines)

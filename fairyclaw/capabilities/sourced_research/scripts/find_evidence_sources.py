# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Step 1 of the sourced-research pipeline: web search for candidate sources."""

from typing import Any, Dict

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext
from fairyclaw.capabilities.sourced_research.config import SourcedResearchRuntimeConfig
from fairyclaw.infrastructure.web.ddgs_client import ddgs_search


def _proxy(context: ToolContext) -> str:
    cfg = expect_group_config(context, SourcedResearchRuntimeConfig)
    raw = cfg.web_proxy or ""
    if raw and "://" not in raw:
        return f"http://{raw}"
    return raw


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Search the web and return a numbered list of candidate source URLs.

    Args:
        args (Dict[str, Any]): Tool arguments: ``query`` (required), ``max_results`` (optional, default 6).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Numbered list of sources (title, URL, snippet) or an error message.
    """
    query = args.get("query")
    if not query:
        return "Error: query is required."

    max_results = int(args.get("max_results", 6))
    proxy_url = _proxy(context) or None

    try:
        results = await ddgs_search(query, max_results=max_results, proxy_url=proxy_url)
    except ImportError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error searching the web: {exc}"

    if not results:
        return f"No sources found for: {query}. Try a more specific query."

    lines = ["Candidate sources (pass these URLs to extract_evidence_excerpt):\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}")
        lines.append(f"    URL: {r.href}")
        lines.append(f"    Snippet: {r.body}\n")

    return "\n".join(lines)

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from typing import Any, Dict

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext
from fairyclaw.capabilities.web_tools.config import WebToolsRuntimeConfig
from fairyclaw.infrastructure.web.ddgs_client import ddgs_search


def _proxy(context: ToolContext) -> str:
    cfg = expect_group_config(context, WebToolsRuntimeConfig)
    raw = cfg.web_proxy or ""
    if raw and "://" not in raw:
        return f"http://{raw}"
    return raw


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Execute DuckDuckGo search and return formatted top results.

    Args:
        args (Dict[str, Any]): Tool arguments containing ``query`` and optional ``max_results``.
        context (ToolContext): Tool runtime context.

    Returns:
        str: Human-readable search results list or standardized error message.
    """
    query = args.get("query")
    if not query:
        return "Error: Query is required."

    max_results = int(args.get("max_results", 5))
    proxy_url = _proxy(context) or None

    try:
        results = await ddgs_search(query, max_results=max_results, proxy_url=proxy_url)
    except ImportError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error performing search: {exc}"

    if not results:
        return f"No results found for query: {query}. Try refining your search or checking your proxy settings."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}\n    Link: {r.href}\n    Snippet: {r.body}\n")
    return "\n".join(lines)

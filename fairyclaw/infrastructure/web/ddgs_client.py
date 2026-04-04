# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""DuckDuckGo search client shared by web capability groups."""

import asyncio
import logging
import os
from functools import partial
from typing import List, Optional

os.environ.setdefault("RUST_LOG", "warn")

for _lib in (
    "httpx", "httpcore", "duckduckgo_search", "urllib3",
    "primp", "h2", "rustls", "hyper_util", "cookie_store",
):
    logging.getLogger(_lib).setLevel(logging.WARNING)

try:
    from ddgs import DDGS  # type: ignore[import-not-found]
    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False

logger = logging.getLogger(__name__)


class SearchResult:
    """Single DuckDuckGo result record."""

    __slots__ = ("title", "href", "body")

    def __init__(self, title: str, href: str, body: str) -> None:
        self.title = title
        self.href = href
        self.body = body


async def ddgs_search(
    query: str,
    max_results: int = 5,
    proxy_url: Optional[str] = None,
) -> List[SearchResult]:
    """Run a DuckDuckGo text search and return structured result objects.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return.
        proxy_url: Optional proxy URL (must include scheme, e.g. ``http://host:port``).

    Returns:
        List of ``SearchResult`` objects; empty list when no results are found.

    Raises:
        ImportError: When the ``duckduckgo-search`` package is not installed.
        RuntimeError: When the upstream search request fails.
    """
    if not _HAS_DDGS:
        raise ImportError(
            "'duckduckgo-search' library is not installed. "
            "Install it with: pip install duckduckgo-search"
        )

    def _search_sync() -> List[dict]:
        kwargs: dict = {}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        with DDGS(**kwargs) as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, _search_sync)
    except Exception as exc:
        logger.error("DDGS search failed: %s", exc, exc_info=True)
        raise RuntimeError(f"DuckDuckGo search failed: {exc}") from exc

    return [
        SearchResult(
            title=r.get("title", "No Title"),
            href=r.get("href", ""),
            body=r.get("body", ""),
        )
        for r in raw
    ]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Shared HTTP page-fetch and text-extraction logic."""

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx

os.environ.setdefault("RUST_LOG", "warn")

for _lib in (
    "httpx", "httpcore", "urllib3", "primp", "h2", "rustls", "hyper_util", "cookie_store",
):
    logging.getLogger(_lib).setLevel(logging.WARNING)

try:
    from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36"
)

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Extracted content from a fetched web page."""

    url: str
    title: str
    text: str
    truncated: bool = False


async def fetch_page_text(
    url: str,
    max_chars: int = 15000,
    proxy_url: Optional[str] = None,
) -> PageContent:
    """Fetch a URL and return extracted readable text.

    Args:
        url: Target URL (``http://`` or ``https://``).
        max_chars: Maximum characters of body text to return before truncation.
        proxy_url: Optional proxy URL (must include scheme).

    Returns:
        ``PageContent`` with ``url``, ``title``, ``text``, and ``truncated`` flag.

    Raises:
        ImportError: When ``beautifulsoup4`` is not installed.
        httpx.HTTPStatusError: On 4xx/5xx responses.
        httpx.RequestError: On network-level failures.
    """
    if not _HAS_BS4:
        raise ImportError(
            "'beautifulsoup4' is not installed. "
            "Install it with: pip install beautifulsoup4"
        )

    if not url.startswith("http"):
        url = "https://" + url

    kwargs: dict[str, Any] = {"follow_redirects": True, "timeout": 30.0}
    if proxy_url:
        kwargs["proxy"] = proxy_url

    async with httpx.AsyncClient(**kwargs) as client:
        response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()

        if "text/html" not in content_type:
            raw = response.text[:max_chars]
            return PageContent(
                url=url,
                title="",
                text=f"Content-Type: {content_type}\n\n{raw}",
                truncated=len(response.text) > max_chars,
            )

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        lines = (line.strip() for line in soup.get_text().splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = "\n".join(chunk for chunk in chunks if chunk)

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n...[Content Truncated]..."

        title = soup.title.string if soup.title else ""
        return PageContent(url=url, title=title or "", text=text, truncated=truncated)

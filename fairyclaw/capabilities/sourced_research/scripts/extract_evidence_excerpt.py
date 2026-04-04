# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Step 2 of the sourced-research pipeline: fetch a page and return a focused excerpt."""

from typing import Any, Dict

import httpx

from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.config.settings import settings
from fairyclaw.infrastructure.web.page_text import fetch_page_text


def _proxy() -> str:
    raw = settings.web_proxy or ""
    if raw and "://" not in raw:
        return f"http://{raw}"
    return raw


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Fetch a source URL and return an excerpt for use as citation evidence.

    Args:
        args (Dict[str, Any]): Tool arguments: ``url`` (required), ``max_chars`` (optional, default 6000).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Structured excerpt block (URL + body text) or an error message.
    """
    url = args.get("url")
    if not url:
        return "Error: url is required."

    max_chars = int(args.get("max_chars", 6000))
    proxy_url = _proxy() or None

    try:
        page = await fetch_page_text(url, max_chars=max_chars, proxy_url=proxy_url)
    except ImportError as exc:
        return f"Error: {exc}"
    except httpx.HTTPStatusError as exc:
        return f"HTTP Error fetching {url}: {exc.response.status_code} - {exc.response.reason_phrase}"
    except httpx.RequestError as exc:
        return f"Network Error fetching {url}: {exc}"
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    header = f"SOURCE EXCERPT\nURL: {page.url}"
    if page.title:
        header += f"\nTitle: {page.title}"
    if page.truncated:
        header += "\n[Note: content was truncated to fit excerpt limit]"

    return f"{header}\n\n{page.text}"

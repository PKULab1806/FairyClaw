# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
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
    """Fetch a webpage and extract readable text content.

    Args:
        args (Dict[str, Any]): Tool arguments containing ``url`` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Extracted page text/metadata or standardized error message.
    """
    url = args.get("url")
    if not url:
        return "Error: URL is required."

    proxy_url = _proxy() or None

    try:
        page = await fetch_page_text(url, max_chars=15000, proxy_url=proxy_url)
    except ImportError as exc:
        return f"Error: {exc}"
    except httpx.HTTPStatusError as exc:
        return f"HTTP Error visiting {url}: {exc.response.status_code} - {exc.response.reason_phrase}"
    except httpx.RequestError as exc:
        return f"Network Error visiting {url}: {exc}"
    except Exception as exc:
        return f"Error visiting {url}: {exc}"

    if page.title:
        return f"Title: {page.title}\nURL: {page.url}\n\n{page.text}"
    return f"URL: {page.url}\n\n{page.text}"

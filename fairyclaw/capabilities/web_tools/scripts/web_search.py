# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
import logging
from typing import Any, Dict
from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.config.settings import settings

# Suppress excessive logging from libraries (including underlying rust/C libraries)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("duckduckgo_search").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("primp").setLevel(logging.WARNING)
logging.getLogger("h2").setLevel(logging.WARNING)
logging.getLogger("rustls").setLevel(logging.WARNING)
logging.getLogger("hyper_util").setLevel(logging.WARNING)
logging.getLogger("cookie_store").setLevel(logging.WARNING)

# Force underlying rust/C libraries (like curl-cffi or primp) to respect env log level
os.environ["RUST_LOG"] = "warn"

# Configure proxy for web search from settings (loaded from fairyclaw.env)
WEB_PROXY = settings.web_proxy or os.environ.get("FAIRYCLAW_WEB_PROXY", "")

# Try to import duckduckgo_search, handle if missing
try:
    from ddgs import DDGS  # type: ignore[import-not-found]
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Execute DuckDuckGo search and return formatted top results.

    Args:
        args (Dict[str, Any]): Tool arguments containing `query` and optional `max_results`.
        context (ToolContext): Tool runtime context.

    Returns:
        str: Human-readable search results list or standardized error message.

    Key Logic:
        - Validates library availability and required query argument.
        - Executes sync search in thread executor to avoid blocking event loop.
        - Formats title/link/snippet triples for downstream model consumption.

    Errors:
        - Returns error when DDGS dependency is unavailable.
        - Returns error when query is missing.
        - Returns error when network or upstream search request fails.
    """
    if not HAS_DDGS:
        return "Error: 'duckduckgo-search' library is not installed. Please install it using `pip install duckduckgo-search`."

    query = args.get("query")
    max_results = args.get("max_results", 5)

    if not query:
        return "Error: Query is required."
        
    # Ensure proxy has scheme if set
    proxy_url = WEB_PROXY
    if proxy_url and "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"

    try:
        # DDGS is synchronous by default in older versions, but recent versions support async or we run it in a thread if needed.
        # However, for simplicity and since it's an IO bound operation, we can use the library's standard method.
        # Note: The latest duckduckgo_search might be sync, so strictly speaking we should run it in an executor 
        # to avoid blocking the async loop, but for a simple tool it's often acceptable or we can wrap it.
        # Let's try to use the async version if available or wrap it.
        
        # Checking library documentation (mental check): DDGS().text() is the method.
        # To be safe and async-compliant in FairyClaw:
        import asyncio
        from functools import partial

        def _search_sync(q, max_r):
            kwargs = {}
            if proxy_url:
                kwargs["proxy"] = proxy_url
            
            try:
                with DDGS(**kwargs) as ddgs:
                    # ddgs.text returns a generator, convert to list
                    return list(ddgs.text(q, max_results=max_r))
            except Exception as e:
                # Log detailed error for debugging, but return a message
                logging.error(f"DDGS Search Error: {str(e)}", exc_info=True)
                raise

        loop = asyncio.get_running_loop()
        # Run in a separate thread to avoid blocking the main event loop
        raw_results = await loop.run_in_executor(None, partial(_search_sync, query, max_results))
        
        if not raw_results:
            return f"No results found for query: {query}. Try refining your search or checking your proxy settings."

        # Format results
        formatted_results = []
        for i, res in enumerate(raw_results, 1):
            title = res.get('title', 'No Title')
            href = res.get('href', '#')
            body = res.get('body', '')
            formatted_results.append(f"[{i}] {title}\n    Link: {href}\n    Snippet: {body}\n")

        return "\n".join(formatted_results)

    except Exception as e:
        return f"Error performing search: {str(e)}"

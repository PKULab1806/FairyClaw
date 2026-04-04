# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
import httpx
import logging
from typing import Any, Dict
from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.config.settings import settings

# Suppress excessive logging from libraries (including underlying rust/C libraries)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("primp").setLevel(logging.WARNING)
logging.getLogger("h2").setLevel(logging.WARNING)
logging.getLogger("rustls").setLevel(logging.WARNING)
logging.getLogger("hyper_util").setLevel(logging.WARNING)
logging.getLogger("cookie_store").setLevel(logging.WARNING)

# Force underlying rust/C libraries to respect env log level
os.environ["RUST_LOG"] = "warn"

# Configure proxy for web search from settings
WEB_PROXY = settings.web_proxy or os.environ.get("FAIRYCLAW_WEB_PROXY", "")

# Try to import BeautifulSoup
try:
    from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Fetch a webpage and extract readable text content.

    Args:
        args (Dict[str, Any]): Tool arguments containing `url` (str).
        context (ToolContext): Tool runtime context.

    Returns:
        str: Extracted page text/metadata or standardized error message.

    Key Logic:
        - Normalizes URL and applies configured proxy when available.
        - Handles non-HTML responses by returning truncated raw text.
        - Uses BeautifulSoup parsing to remove noisy tags and compact text.

    Errors:
        - Returns error when URL is missing.
        - Returns dependency error when BeautifulSoup is unavailable for HTML parsing.
        - Returns network/HTTP errors with response status context.
    """
    url = args.get("url")
    if not url:
        return "Error: URL is required."

    # Validate URL loosely
    if not url.startswith("http"):
        url = "https://" + url
        
    # Ensure proxy has scheme if set
    proxy_url = WEB_PROXY
    if proxy_url and "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"

    try:
        kwargs: dict[str, Any] = {"follow_redirects": True, "timeout": 30.0}
        if proxy_url:
            kwargs["proxy"] = proxy_url
            
        async with httpx.AsyncClient(**kwargs) as client:
            # Add a User-Agent to avoid being blocked by some sites
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            content_type = response.headers.get("content-type", "").lower()
            
            # If it's not HTML (e.g., JSON, plain text), return raw text
            if "text/html" not in content_type:
                return f"Content-Type: {content_type}\n\n{response.text[:10000]}..." # Truncate if too long

            html_content = response.text

            if HAS_BS4:
                soup = BeautifulSoup(html_content, "html.parser")
                
                # Remove script and style elements
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.decompose()
                
                # Get text
                text = soup.get_text()
                
                # Break into lines and remove leading/trailing space on each
                lines = (line.strip() for line in text.splitlines())
                # Break multi-headlines into a line each
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                # Drop blank lines
                text = '\n'.join(chunk for chunk in chunks if chunk)
                
                # Limit length to avoid blowing up context
                if len(text) > 15000:
                    text = text[:15000] + "\n...[Content Truncated]..."
                
                return f"Title: {soup.title.string if soup.title else 'No Title'}\nURL: {url}\n\n{text}"
            else:
                # Fallback if BS4 is not installed (simple regex strip)
                return "Error: 'beautifulsoup4' is not installed. Please install it for better parsing."

    except httpx.HTTPStatusError as e:
        return f"HTTP Error visiting {url}: {e.response.status_code} - {e.response.reason_phrase}"
    except httpx.RequestError as e:
        return f"Network Error visiting {url}: {str(e)}"
    except Exception as e:
        return f"Error visiting {url}: {str(e)}"

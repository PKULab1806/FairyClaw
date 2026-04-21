# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import os
import re
import mimetypes
import asyncio
from typing import Any, Dict
from urllib.parse import urlparse, unquote

import httpx

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path
from fairyclaw_plugins.web_tools.config import WebToolsRuntimeConfig
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _normalize_url(raw_url: str) -> str:
    """Normalize URL and auto-prepend https scheme when missing.

    Args:
        raw_url (str): Raw user-provided URL string.

    Returns:
        str: Normalized URL.
    """
    url = raw_url.strip()
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


def _extract_filename_from_disposition(disposition: str) -> str:
    """Extract filename token from Content-Disposition header.

    Args:
        disposition (str): Raw Content-Disposition header value.

    Returns:
        str: Parsed filename or empty string when unavailable.
    """
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', disposition, flags=re.IGNORECASE)
    if not match:
        return ""
    return unquote(match.group(1).strip())


def _safe_filename(name: str) -> str:
    """Sanitize filename by removing path separators and empty fallback.

    Args:
        name (str): Candidate filename.

    Returns:
        str: Safe basename string.
    """
    cleaned = name.replace("\\", "/").split("/")[-1].strip()
    return cleaned or "downloaded_file"


def _decide_filename(url: str, headers: httpx.Headers, explicit_filename: str | None, content_type: str) -> str:
    """Determine final filename from explicit value, headers, URL, and mime.

    Args:
        url (str): Download URL.
        headers (httpx.Headers): Response headers.
        explicit_filename (str | None): Optional user-specified filename.
        content_type (str): Response content type.

    Returns:
        str: Final filename for saving.
    """
    if explicit_filename and explicit_filename.strip():
        filename = _safe_filename(explicit_filename)
    else:
        disposition = headers.get("content-disposition", "")
        from_disposition = _extract_filename_from_disposition(disposition) if disposition else ""
        if from_disposition:
            filename = _safe_filename(from_disposition)
        else:
            path_name = _safe_filename(unquote(os.path.basename(urlparse(url).path)))
            filename = path_name if path_name and path_name != "downloaded_file" else "downloaded_file"
    if "." not in filename:
        guessed_ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) if content_type else None
        if guessed_ext:
            filename = f"{filename}{guessed_ext}"
    return filename


def _build_download_header_attempts(url: str) -> list[dict[str, str]]:
    """Build progressive request header sets for anti-bot compatibility.

    Args:
        url (str): Download URL.

    Returns:
        list[dict[str, str]]: Ordered request-header attempts.
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    base = {
        "User-Agent": BROWSER_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Connection": "keep-alive",
    }
    if origin:
        base["Referer"] = f"{origin}/"
        base["Origin"] = origin
    attempt_2 = dict(base)
    attempt_2.update(
        {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    attempt_3 = dict(attempt_2)
    attempt_3["Range"] = "bytes=0-"
    return [base, attempt_2, attempt_3]


async def _warmup_origin(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> None:
    """Warm up target origin to improve blocked-download success probability.

    Args:
        client (httpx.AsyncClient): Reused async HTTP client.
        url (str): Download URL.
        headers (dict[str, str]): Header template.

    Returns:
        None
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    warmup_headers = dict(headers)
    warmup_headers.pop("Range", None)
    try:
        resp = await client.get(origin, headers=warmup_headers)
        _ = resp.status_code
    except Exception:
        return


async def _download_once(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    max_bytes: int,
    explicit_filename: str | None,
) -> tuple[bytes, str, str]:
    """Download file content once and enforce size limit.

    Args:
        client (httpx.AsyncClient): Reused async HTTP client.
        url (str): Download URL.
        headers (dict[str, str]): Request headers.
        max_bytes (int): Maximum permitted payload size.
        explicit_filename (str | None): Optional filename override.

    Returns:
        tuple[bytes, str, str]: Downloaded bytes, content_type, resolved filename.

    Raises:
        ValueError: Raised when streamed content exceeds max_bytes.
        httpx.HTTPStatusError: Raised for non-2xx responses.
    """
    total = 0
    chunks: list[bytes] = []
    content_type = ""
    filename = "downloaded_file"
    async with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").strip()
        filename = _decide_filename(url, response.headers, explicit_filename, content_type)
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"file exceeds max_bytes limit ({max_bytes} bytes)")
            chunks.append(chunk)
    return b"".join(chunks), content_type, filename


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Download remote file and save it to local filesystem root.

    Args:
        args (Dict[str, Any]): Tool arguments containing url, target_path, optional filename/max_bytes.
        context (ToolContext): Tool runtime context.

    Returns:
        str: Success summary with saved path and metadata, or standardized error text.

    Key Logic:
        - Normalizes URL and validates scheme/size parameters.
        - Tries multiple browser-like header combinations for blocked sites.
        - Validates destination path against configured filesystem root before writing.

    Errors:
        - Returns argument validation errors for missing/invalid inputs.
        - Returns HTTP/network errors when remote download fails.
        - Returns path validation and local write errors on save stage.
    """
    raw_url = str(args.get("url") or "").strip()
    target_path = str(args.get("target_path") or "").strip()
    if not raw_url:
        return "Error: url is required."
    if not target_path:
        return "Error: target_path is required."
    max_bytes_raw = args.get("max_bytes", 20971520)
    try:
        max_bytes = int(max_bytes_raw)
    except Exception:
        return "Error: max_bytes must be an integer."
    if max_bytes <= 0:
        return "Error: max_bytes must be greater than 0."

    url = _normalize_url(raw_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "Error: only http/https URLs are supported."
    cfg = expect_group_config(context, WebToolsRuntimeConfig)
    root_dir = context.filesystem_root_dir
    if not root_dir:
        return "Error: FAIRYCLAW_FILESYSTEM_ROOT_DIR is not configured."

    raw_proxy = cfg.web_proxy or ""
    proxy_url = raw_proxy
    if proxy_url and "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"

    filename_arg = args.get("filename")
    content = b""
    content_type = ""
    filename = "downloaded_file"

    try:
        client_kwargs: dict[str, Any] = {"follow_redirects": True, "timeout": 60.0}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        attempts = _build_download_header_attempts(url)
        explicit_filename = str(filename_arg) if filename_arg is not None else None
        last_http_error: httpx.HTTPStatusError | None = None
        last_req_error: httpx.RequestError | None = None
        async with httpx.AsyncClient(**client_kwargs) as client:
            for i, headers in enumerate(attempts):
                try:
                    content, content_type, filename = await _download_once(
                        client=client,
                        url=url,
                        headers=headers,
                        max_bytes=max_bytes,
                        explicit_filename=explicit_filename,
                    )
                    break
                except ValueError as size_exc:
                    return f"Error: {size_exc}."
                except httpx.HTTPStatusError as e:
                    last_http_error = e
                    if e.response.status_code == 403 and i < len(attempts) - 1:
                        await _warmup_origin(client, url, headers)
                        await asyncio.sleep(0.6 * (i + 1))
                        continue
                    if e.response.status_code in {429, 500, 502, 503, 504} and i < len(attempts) - 1:
                        await asyncio.sleep(0.6 * (i + 1))
                        continue
                    break
                except httpx.RequestError as e:
                    last_req_error = e
                    if i < len(attempts) - 1:
                        await asyncio.sleep(0.6 * (i + 1))
                        continue
                    break
        if not content:
            if last_http_error is not None:
                status = last_http_error.response.status_code
                reason = last_http_error.response.reason_phrase
                if status == 403:
                    return f"HTTP Error downloading {url}: 403 - Forbidden (retried with browser headers/referer but still blocked)"
                return f"HTTP Error downloading {url}: {status} - {reason}"
            if last_req_error is not None:
                return f"Network Error downloading {url}: {str(last_req_error)}"
            return f"Error downloading {url}: unknown download failure"
        candidate_path = target_path
        if target_path.endswith(os.sep) or (os.path.exists(target_path) and os.path.isdir(target_path)):
            candidate_path = os.path.join(target_path, filename)
        safe_path, error = resolve_safe_path(candidate_path, root_dir, context.workspace_root)
        if error or safe_path is None:
            return error or "Error: Invalid target_path."
        abs_path = safe_path.path
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as f:
            f.write(content)
        mime_label = content_type or "unknown"
        return f"Downloaded successfully. saved_path: {abs_path}, filename: {os.path.basename(abs_path)}, size: {len(content)} bytes, mime_type: {mime_label}."
    except httpx.HTTPStatusError as e:
        return f"HTTP Error downloading {url}: {e.response.status_code} - {e.response.reason_phrase}"
    except httpx.RequestError as e:
        return f"Network Error downloading {url}: {str(e)}"
    except Exception as e:
        return f"Error downloading {url}: {str(e)}"

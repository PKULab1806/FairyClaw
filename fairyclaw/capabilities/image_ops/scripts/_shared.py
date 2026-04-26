# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Shared helpers for ImageOps tools.

Image ``input_ref`` contract (``inject_image_context`` / ``generate_or_edit_image``):

- **Exactly one** of ``file_path``, ``file_id``, or ``url``.
- **file_path**: absolute path on the server filesystem (``/...``). ``file:///...`` is accepted and
  normalized internally. Must resolve under ``ToolContext`` roots via ``resolve_safe_path``.
- **file_id**: session binary row id only — string must start with ``file_`` followed by hex digits
  (from ``send_file`` / ``list_session_files``). Chat-native ids (e.g. ``imgu_...``) are **not** DB files.
- **url**: either ``data:image/...;base64,...`` or ``http(s)://`` URL whose response body is an image
  (bounded by ``max_bytes``). Prefer ``file_path`` when the file is already on disk.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from typing import Any

from fairyclaw.infrastructure.database.repository import FileRepository
from fairyclaw.infrastructure.media.image_compress import compress_image_for_context
from fairyclaw.infrastructure.uri_paths import normalize_file_uri_to_path
from fairyclaw.sdk.tools import ToolContext, get_context_db, resolve_safe_path


@dataclass(frozen=True)
class ImagePayload:
    """Resolved image binary payload with metadata."""

    raw_bytes: bytes
    mime: str
    source: str
    source_kind: str


def _guess_mime_from_bytes(raw: bytes) -> str | None:
    try:
        import filetype  # type: ignore[import-untyped]
    except Exception:
        return None
    kind = filetype.guess(raw)
    if kind and kind.mime.startswith("image/"):
        return kind.mime
    return None


async def _download_image_from_http(url: str, max_bytes: int | None) -> tuple[bytes, str] | tuple[None, str]:
    """GET ``url`` and return ``(raw_bytes, mime)`` or ``(None, error)``."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": "FairyclawImageTool/1.0"})
            resp.raise_for_status()
        except Exception as exc:
            return None, f"Error: HTTP fetch failed for input_ref.url: {exc}"
        raw = bytes(resp.content or b"")
        if not raw:
            return None, "Error: empty HTTP body when fetching input_ref.url."
        if max_bytes is not None and len(raw) > max_bytes:
            return None, f"Error: image size {len(raw)} exceeds max_bytes={max_bytes}."
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        mime: str | None = ctype if ctype.startswith("image/") else None
        if not mime or not mime.startswith("image/"):
            mime, _ = mimetypes.guess_type(url.split("?", 1)[0])
        if not mime or not mime.startswith("image/"):
            mime = _guess_mime_from_bytes(raw)
        if not mime or not mime.startswith("image/"):
            return None, "Error: URL did not resolve to an image (check Content-Type or file extension)."
        return raw, mime


def parse_input_ref(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    input_ref = args.get("input_ref")
    if not isinstance(input_ref, dict):
        return None, "Error: input_ref must be an object."
    keys = [k for k in ("file_path", "file_id", "url") if isinstance(input_ref.get(k), str) and input_ref.get(k).strip()]
    if len(keys) != 1:
        return None, "Error: input_ref must contain exactly one of file_path/file_id/url."
    return input_ref, None


async def resolve_image_payload(
    context: ToolContext,
    *,
    input_ref: dict[str, Any],
    mime_hint: str | None = None,
    max_bytes: int | None = None,
) -> tuple[ImagePayload | None, str | None]:
    """Resolve one image payload from ``file_path``, ``file_id``, or ``url`` (see module docstring)."""
    if "file_path" in input_ref and isinstance(input_ref["file_path"], str):
        root_dir = context.filesystem_root_dir
        fp = normalize_file_uri_to_path(str(input_ref["file_path"]))
        safe_path, error = resolve_safe_path(fp, root_dir, context.workspace_root)
        if error or safe_path is None:
            return None, error or "Error: invalid file_path."
        abs_path = safe_path.path
        if not os.path.isfile(abs_path):
            return None, f"Error: file_path does not exist or is not a file: {abs_path}"
        raw = open(abs_path, "rb").read()
        if max_bytes is not None and len(raw) > max_bytes:
            return None, f"Error: image size {len(raw)} exceeds max_bytes={max_bytes}."
        mime, _ = mimetypes.guess_type(abs_path)
        if not mime or not mime.startswith("image/"):
            mime = _guess_mime_from_bytes(raw) or (mime_hint or "image/png")
        return ImagePayload(raw_bytes=raw, mime=mime, source=abs_path, source_kind="file_path"), None

    if "file_id" in input_ref and isinstance(input_ref["file_id"], str):
        fid = str(input_ref["file_id"]).strip()
        if not fid.startswith("file_"):
            return None, (
                "Error: file_id must be a session upload id starting with `file_` (copy from send_file / "
                "list_session_files). IDs like `imgu_...` from chat UIs are not session files."
            )
        db, error = get_context_db(context)
        if error:
            return None, error
        repo = FileRepository(db)
        model = await repo.get_for_session(file_id=fid, session_id=context.session_id)
        if model is None:
            return None, f"Error: file_id not found in current session: {fid}"
        raw = model.content or b""
        if max_bytes is not None and len(raw) > max_bytes:
            return None, f"Error: image size {len(raw)} exceeds max_bytes={max_bytes}."
        mime = model.mime_type if isinstance(model.mime_type, str) else None
        if not mime or not mime.startswith("image/"):
            mime = _guess_mime_from_bytes(raw) or (mime_hint or "image/png")
        return ImagePayload(raw_bytes=raw, mime=mime, source=model.id, source_kind="file_id"), None

    if "url" in input_ref and isinstance(input_ref["url"], str):
        url = input_ref["url"].strip()
        if not url:
            return None, "Error: input_ref.url is empty."
        if url.startswith("data:image/"):
            head, _, body = url.partition(",")
            if ";base64" not in head:
                return None, "Error: data URL must be base64 encoded."
            raw = base64.b64decode(body)
            if max_bytes is not None and len(raw) > max_bytes:
                return None, f"Error: image size {len(raw)} exceeds max_bytes={max_bytes}."
            mime = head[5:].split(";")[0] or "image/png"
            return ImagePayload(raw_bytes=raw, mime=mime, source=url[:64] + "...", source_kind="url"), None
        if url.startswith(("http://", "https://")):
            got = await _download_image_from_http(url, max_bytes)
            if got[0] is None:
                return None, got[1]
            raw, mime = got[0], got[1]
            return ImagePayload(raw_bytes=raw, mime=mime, source=url[:120], source_kind="url"), None
        return None, "Error: input_ref.url must be data:image/...;base64,... or http(s):// link to an image."

    return None, "Error: unsupported input_ref."


def to_data_url(payload: ImagePayload) -> str:
    """Convert image payload to data URL."""
    b64 = base64.b64encode(payload.raw_bytes).decode("utf-8")
    return f"data:{payload.mime};base64,{b64}"


def to_context_data_url(
    payload: ImagePayload,
    *,
    image_max_edge: int = 768,
    image_jpeg_quality: int = 55,
    image_png_compress_level: int = 9,
) -> tuple[str, bytes, str]:
    """Convert payload to a context-friendly data URL with image compression when possible."""
    optimized_bytes, optimized_mime = compress_image_for_context(
        payload.raw_bytes,
        payload.mime,
        image_max_edge=image_max_edge,
        image_jpeg_quality=image_jpeg_quality,
        image_png_compress_level=image_png_compress_level,
    )
    b64 = base64.b64encode(optimized_bytes).decode("utf-8")
    return f"data:{optimized_mime};base64,{b64}", optimized_bytes, optimized_mime


def payload_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

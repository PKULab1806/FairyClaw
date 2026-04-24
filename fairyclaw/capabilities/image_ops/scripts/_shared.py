# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Shared helpers for ImageOps tools."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from typing import Any

from fairyclaw.infrastructure.database.repository import FileRepository
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
    """Resolve one image payload from file path, session file_id, or URL."""
    if "file_path" in input_ref and isinstance(input_ref["file_path"], str):
        root_dir = context.filesystem_root_dir
        safe_path, error = resolve_safe_path(str(input_ref["file_path"]), root_dir, context.workspace_root)
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
        db, error = get_context_db(context)
        if error:
            return None, error
        repo = FileRepository(db)
        model = await repo.get_for_session(file_id=input_ref["file_id"], session_id=context.session_id)
        if model is None:
            return None, f"Error: file_id not found in current session: {input_ref['file_id']}"
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
        return None, "Error: Only data:image/... URLs are supported for deterministic local processing."

    return None, "Error: unsupported input_ref."


def to_data_url(payload: ImagePayload) -> str:
    """Convert image payload to data URL."""
    b64 = base64.b64encode(payload.raw_bytes).decode("utf-8")
    return f"data:{payload.mime};base64,{b64}"


def payload_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

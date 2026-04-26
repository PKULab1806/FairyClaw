# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Generate or edit images via configured image-generation LLM endpoint."""

from __future__ import annotations

import json
import mimetypes
import os
from typing import Any

from fairyclaw.infrastructure.llm.factory import create_llm_client
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path

from ._shared import parse_input_ref, resolve_image_payload

DEFAULT_IMAGE_PROFILE = "image_generation"


def _guess_mime_from_output_path(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if isinstance(mime, str) and mime.startswith("image/"):
        return mime
    return "image/png"


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    """Call image_generation endpoint and persist output image."""
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return "Error: prompt is required."

    output_path = args.get("output_path")
    if not isinstance(output_path, str) or not output_path.strip():
        return "Error: output_path is required."
    safe_out, path_error = resolve_safe_path(output_path, context.filesystem_root_dir, context.workspace_root)
    if path_error or safe_out is None:
        return path_error or "Error: invalid output_path."

    input_ref, parse_error = parse_input_ref(args) if args.get("input_ref") is not None else (None, None)
    if parse_error:
        return parse_error

    input_bytes = b""
    input_mime = "image/png"
    source_kind = "none"
    if input_ref is not None:
        payload, resolve_error = await resolve_image_payload(
            context,
            input_ref=input_ref,
            max_bytes=20 * 1024 * 1024,
        )
        if resolve_error:
            return resolve_error
        assert payload is not None
        input_bytes = payload.raw_bytes
        input_mime = payload.mime
        source_kind = payload.source_kind

    profile_name = str(args.get("profile_name") or DEFAULT_IMAGE_PROFILE).strip() or DEFAULT_IMAGE_PROFILE
    raw_size = args.get("size")
    size = str(raw_size).strip() if raw_size is not None else None
    if size == "":
        size = None

    try:
        client = create_llm_client(profile_name)
    except Exception as exc:
        return f"Error: cannot create image profile '{profile_name}': {exc}"

    if not client.is_available():
        return f"Error: API key for profile '{profile_name}' is not configured."

    try:
        image_bytes = await client.generate_image_edit(
            prompt=prompt,
            input_image_bytes=input_bytes,
            input_mime=input_mime,
            size=size,
        )
    except Exception as exc:
        return f"Error: image endpoint call failed: {exc}"

    try:
        parent_dir = os.path.dirname(safe_out.path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(safe_out.path, "wb") as f:
            f.write(image_bytes)
    except Exception as exc:
        return f"Error: failed to write output image: {exc}"

    result = {
        "ok": True,
        "profile_name": profile_name,
        "output_path": safe_out.path,
        "bytes": len(image_bytes),
        "mime": _guess_mime_from_output_path(safe_out.path),
        "source_kind": source_kind,
    }
    return json.dumps(result, ensure_ascii=False)

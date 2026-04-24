# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Inject image context as an image_url segment."""

from __future__ import annotations

import json
from typing import Any

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.sdk.tools import ToolContext
from fairyclaw.sdk.types import ContentSegment

try:
    from fairyclaw_plugins.image_ops.config import ImageOpsRuntimeConfig
except Exception:  # pragma: no cover - direct test imports may bypass plugin loader
    from fairyclaw.capabilities.image_ops.config import ImageOpsRuntimeConfig

from ._shared import parse_input_ref, payload_sha256, resolve_image_payload, to_data_url


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    """Build data-URL image segment and optionally persist it into session history."""
    input_ref, err = parse_input_ref(args)
    if err:
        return err

    cfg = expect_group_config(context, ImageOpsRuntimeConfig)
    max_bytes = args.get("max_bytes")
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        max_bytes = cfg.max_image_bytes
    mime_hint = args.get("mime_hint") if isinstance(args.get("mime_hint"), str) else None
    append_text_hint = args.get("append_text_hint") if isinstance(args.get("append_text_hint"), str) else None
    scope = str(args.get("scope") or "current_turn").strip()
    if scope not in {"current_turn", "session_memory"}:
        return "Error: scope must be one of current_turn/session_memory."

    payload, err = await resolve_image_payload(
        context,
        input_ref=input_ref or {},
        mime_hint=mime_hint,
        max_bytes=max_bytes,
    )
    if err:
        return err
    assert payload is not None

    data_url = to_data_url(payload)
    image_segment = ContentSegment.image_url_segment(data_url)
    segment_dict = image_segment.to_dict()

    persisted = False
    if scope == "session_memory":
        if context.memory is None:
            return "Error: scope=session_memory requires memory context."
        segments = [image_segment]
        if append_text_hint:
            segments.insert(0, ContentSegment.text_segment(append_text_hint))
        message = SessionMessageBlock.from_segments(SessionMessageRole.USER, tuple(segments))
        if message is None:
            return "Error: failed to construct session message for image context."
        await context.memory.add_session_event(session_id=context.session_id, message=message)
        persisted = True

    result = {
        "ok": True,
        "content_segment": segment_dict,
        "bytes": len(payload.raw_bytes),
        "mime": payload.mime,
        "sha256": payload_sha256(payload.raw_bytes),
        "source_kind": payload.source_kind,
        "source": payload.source,
        "scope": scope,
        "persisted": persisted,
        "truncated": False,
    }
    return json.dumps(result, ensure_ascii=False)

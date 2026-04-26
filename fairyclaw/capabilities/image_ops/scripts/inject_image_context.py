# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Inject image context as an image_url segment."""

from __future__ import annotations

import json
from typing import Any

from fairyclaw.sdk.ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.sdk.tools import ToolContext
from fairyclaw.sdk.types import ContentSegment

from ._shared import parse_input_ref, payload_sha256, resolve_image_payload, to_context_data_url


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    """Persist one image as session history context for later turns."""
    input_ref, err = parse_input_ref(args)
    if err:
        return err

    payload, err = await resolve_image_payload(
        context,
        input_ref=input_ref or {},
        max_bytes=8 * 1024 * 1024,
    )
    if err:
        return err
    assert payload is not None

    if context.memory is None:
        return "Error: inject_image_context requires memory context."

    data_url, optimized_bytes, optimized_mime = to_context_data_url(payload)
    image_segment = ContentSegment.image_url_segment(data_url)
    message = SessionMessageBlock.from_segments(SessionMessageRole.USER, (image_segment,))
    if message is None:
        return "Error: failed to construct session message for image context."
    await context.memory.add_session_event(session_id=context.session_id, message=message)

    result = {
        "ok": True,
        "bytes": len(optimized_bytes),
        "mime": optimized_mime,
        "sha256": payload_sha256(optimized_bytes),
        "original_bytes": len(payload.raw_bytes),
        "original_mime": payload.mime,
        "source_kind": payload.source_kind,
        "source": payload.source,
        "persisted": True,
    }
    return json.dumps(result, ensure_ascii=False)

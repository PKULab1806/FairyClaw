#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Reload previously unloaded segments back into session history."""

from __future__ import annotations

import json
from typing import Any

from fairyclaw.capabilities.compression_hooks.scripts._unloaded_segments_state import (
    consume_unloaded_segment_records,
)
from fairyclaw.sdk.ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.sdk.tools import ToolContext
from fairyclaw.sdk.types import ContentSegment


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    """Restore unloaded segments as fresh session messages."""
    if context.memory is None:
        return "Error: reload_unloaded_segments requires memory context."

    mode = str(args.get("mode") or "latest").strip().lower()
    if mode not in {"latest", "all", "ids"}:
        return "Error: mode must be one of latest/all/ids."
    unload_ids = args.get("unload_ids")
    if unload_ids is not None and not isinstance(unload_ids, list):
        return "Error: unload_ids must be an array of strings."
    limit_raw = args.get("limit", 1)
    try:
        limit = max(1, int(limit_raw))
    except (TypeError, ValueError):
        limit = 1

    records = consume_unloaded_segment_records(
        session_id=context.session_id,
        mode=mode,
        unload_ids=[str(v) for v in unload_ids or []],
        limit=limit,
    )
    if not records:
        return "Error: no unloaded segments available for reload."

    restored_ids: list[str] = []
    restored_count = 0
    for record in records:
        raw_segments = record.get("segments")
        if not isinstance(raw_segments, list):
            continue
        segments: list[ContentSegment] = []
        for raw in raw_segments:
            if not isinstance(raw, dict):
                continue
            try:
                segments.append(ContentSegment.from_dict(raw))
            except Exception:
                continue
        if not segments:
            continue
        role = SessionMessageRole.from_value(str(record.get("role") or "user"))
        message = SessionMessageBlock.from_segments(role, tuple(segments))
        if message is None:
            continue
        await context.memory.add_session_event(session_id=context.session_id, message=message)
        restored_ids.append(str(record.get("unload_id") or ""))
        restored_count += 1

    if restored_count <= 0:
        return "Error: failed to reconstruct unloaded segments."

    return json.dumps(
        {
            "ok": True,
            "persisted": True,
            "restored_count": restored_count,
            "reloaded_ids": restored_ids,
        },
        ensure_ascii=False,
    )

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fairyclaw.capabilities.image_ops.config import ImageOpsRuntimeConfig
from fairyclaw.capabilities.image_ops.scripts.apply_image_style import execute as apply_style_execute
from fairyclaw.capabilities.image_ops.scripts.compose_subject_background import execute as compose_execute
from fairyclaw.capabilities.image_ops.scripts.inject_image_context import execute as inject_execute
from fairyclaw.sdk.tools import ToolContext


class _DummyMemory:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def add_session_event(self, *, session_id: str, message: object, **_: object) -> None:
        self.events.append((session_id, message))


def _build_context(tmp_path: Path, memory: object | None = None) -> ToolContext:
    return ToolContext(
        session_id="sess_test_image_ops",
        memory=memory,
        planner=None,
        group_runtime_config=ImageOpsRuntimeConfig(),
        filesystem_root_dir=str(tmp_path),
        workspace_root=str(tmp_path),
        runtime_context=None,
    )


def _make_sample_png(tmp_path: Path, name: str = "in.png") -> Path:
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("RGB", (160, 120), (60, 130, 220))
    p = tmp_path / name
    img.save(p, format="PNG")
    return p


def test_inject_image_context_current_turn(tmp_path: Path) -> None:
    image_path = _make_sample_png(tmp_path)
    context = _build_context(tmp_path)
    raw = asyncio.run(
        inject_execute(
            {
                "input_ref": {"file_path": str(image_path)},
                "scope": "current_turn",
            },
            context,
        )
    )
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["persisted"] is False
    assert result["content_segment"]["type"] == "image_url"
    assert str(result["content_segment"]["image_url"]["url"]).startswith("data:image/")


def test_inject_image_context_session_memory_persists(tmp_path: Path) -> None:
    image_path = _make_sample_png(tmp_path, "persist.png")
    memory = _DummyMemory()
    context = _build_context(tmp_path, memory=memory)
    raw = asyncio.run(
        inject_execute(
            {
                "input_ref": {"file_path": str(image_path)},
                "scope": "session_memory",
                "append_text_hint": "Analyze this image.",
            },
            context,
        )
    )
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["persisted"] is True
    assert len(memory.events) == 1


def test_apply_style_and_compose_outputs(tmp_path: Path) -> None:
    src = _make_sample_png(tmp_path, "sample_subject.png")
    context = _build_context(tmp_path)

    styled_out = tmp_path / "out" / "subject_styled.png"
    styled_raw = asyncio.run(
        apply_style_execute(
            {
                "input_ref": {"file_path": str(src)},
                "output_path": str(styled_out),
                "style_profile": "toon_v1",
                "intensity": 0.8,
            },
            context,
        )
    )
    styled = json.loads(styled_raw)
    assert styled["ok"] is True
    assert styled_out.is_file()
    assert styled_out.stat().st_size > 0

    composed_out = tmp_path / "out" / "subject_scene.png"
    composed_raw = asyncio.run(
        compose_execute(
            {
                "input_ref": {"file_path": str(src)},
                "output_path": str(composed_out),
                "background_spec": {"type": "preset", "id_or_params": {"id": "space"}},
                "placement": {"anchor": "center"},
            },
            context,
        )
    )
    composed = json.loads(composed_raw)
    assert composed["ok"] is True
    assert composed_out.is_file()
    assert composed_out.stat().st_size > 0

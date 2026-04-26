# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from fairyclaw.capabilities.image_ops.scripts.generate_or_edit_image import execute as generate_execute
from fairyclaw.capabilities.image_ops.scripts.image_tools_availability_gate import execute_hook as gate_execute
from fairyclaw.capabilities.image_ops.scripts.inject_image_context import execute as inject_execute
from fairyclaw.capabilities.compression_hooks.scripts.compression_tools_availability_gate import (
    execute_hook as compression_gate_execute,
)
from fairyclaw.capabilities.compression_hooks.scripts.reload_unloaded_segments import execute as reload_execute
from fairyclaw.capabilities.compression_hooks.scripts._unloaded_segments_state import save_unloaded_segments_state
from fairyclaw.sdk.hooks import (
    HookExecutionContext,
    HookStage,
    HookStageInput,
    LlmFunctionToolSpec,
    ToolsPreparedHookPayload,
)
from fairyclaw.sdk.tools import ToolContext


class _DummyMemory:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def add_session_event(self, *, session_id: str, message: object, **_: object) -> None:
        self.events.append((session_id, message))


class _DummyImageClient:
    def __init__(self, available: bool = True, output: bytes | None = None) -> None:
        self._available = available
        self._output = output or b"\x89PNG\r\n\x1a\ndummy"

    def is_available(self) -> bool:
        return self._available

    async def generate_image_edit(self, **_: object) -> bytes:
        return self._output


def _build_context(tmp_path: Path, memory: object | None = None, cfg: object | None = None) -> ToolContext:
    return ToolContext(
        session_id="sess_test_image_ops",
        memory=memory,
        planner=None,
        group_runtime_config=cfg,
        filesystem_root_dir=str(tmp_path),
        workspace_root=str(tmp_path),
        runtime_context=None,
    )


def _make_sample_png(tmp_path: Path, name: str = "in.png") -> Path:
    p = tmp_path / name
    # 1x1 transparent PNG
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO7Z7xkAAAAASUVORK5CYII="
    )
    p.write_bytes(base64.b64decode(png_b64))
    return p


def test_inject_image_context_persists_without_returning_image_url(tmp_path: Path) -> None:
    image_path = _make_sample_png(tmp_path, "persist.png")
    memory = _DummyMemory()
    context = _build_context(tmp_path, memory=memory)
    raw = asyncio.run(
        inject_execute(
            {
                "input_ref": {"file_path": str(image_path)},
            },
            context,
        )
    )
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["persisted"] is True
    assert "content_segment" not in result
    assert len(memory.events) == 1


def test_generate_or_edit_image_writes_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _make_sample_png(tmp_path, "subject.png")
    context = _build_context(tmp_path)
    out = tmp_path / "out" / "generated.png"

    from fairyclaw.capabilities.image_ops.scripts import generate_or_edit_image as mod

    monkeypatch.setattr(mod, "create_llm_client", lambda _profile: _DummyImageClient())
    raw = asyncio.run(
        generate_execute(
            {
                "prompt": "make a transformed variant",
                "input_ref": {"file_path": str(src)},
                "output_path": str(out),
            },
            context,
        ),
    )
    result = json.loads(raw)
    assert result["ok"] is True
    assert out.is_file()
    assert out.stat().st_size > 0


def test_generate_image_without_input_ref_writes_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _build_context(tmp_path)
    out = tmp_path / "out" / "generated_no_input.png"

    from fairyclaw.capabilities.image_ops.scripts import generate_or_edit_image as mod

    monkeypatch.setattr(mod, "create_llm_client", lambda _profile: _DummyImageClient())
    raw = asyncio.run(
        generate_execute(
            {
                "prompt": "generate a kitten on beach",
                "output_path": str(out),
            },
            context,
        ),
    )
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["source_kind"] == "none"
    assert out.is_file()
    assert out.stat().st_size > 0


def test_image_tool_gate_hides_generate_tool_when_profile_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_generate = LlmFunctionToolSpec(name="generate_or_edit_image", description="", parameters={})
    tool_inject = LlmFunctionToolSpec(name="inject_image_context", description="", parameters={})
    payload = ToolsPreparedHookPayload(
        session_id="sess_x",
        task_type="image",
        is_sub_session=True,
        enabled_groups=["ImageOps"],
        tools=[tool_generate, tool_inject],
    )
    hook_input = HookStageInput(
        stage=HookStage.TOOLS_PREPARED,
        context=HookExecutionContext(
            session_id="sess_x",
            turn_id="turn_x",
            task_type="image",
            is_sub_session=True,
            enabled_groups=["ImageOps"],
        ),
        payload=payload,
    )

    class _Cfg:
        profiles: dict[str, object] = {}

    from fairyclaw.capabilities.image_ops.scripts import image_tools_availability_gate as gate_mod

    monkeypatch.setattr(gate_mod, "load_llm_endpoint_config", lambda: _Cfg())
    result = asyncio.run(gate_execute(hook_input))
    assert result.patched_payload is not None
    names = [tool.name for tool in result.patched_payload.tools]
    assert "generate_or_edit_image" not in names
    assert "inject_image_context" in names


def test_compression_tool_gate_keeps_reload_tool_when_unloaded_segments_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_reload = LlmFunctionToolSpec(name="reload_unloaded_segments", description="", parameters={})
    payload = ToolsPreparedHookPayload(
        session_id="sess_reload",
        task_type="image",
        is_sub_session=True,
        enabled_groups=["CompressionHooks"],
        tools=[tool_reload],
    )
    hook_input = HookStageInput(
        stage=HookStage.TOOLS_PREPARED,
        context=HookExecutionContext(
            session_id="sess_reload",
            turn_id="turn_reload",
            task_type="image",
            is_sub_session=True,
            enabled_groups=["CompressionHooks"],
        ),
        payload=payload,
    )
    save_unloaded_segments_state(
        session_id="sess_reload",
        state={
            "records": [
                {
                    "unload_id": "imgu_1",
                    "role": "user",
                    "segments": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
                    "restored": False,
                }
            ]
        },
        memory_root=str(tmp_path),
    )
    from fairyclaw.capabilities.compression_hooks.scripts import compression_tools_availability_gate as gate_mod

    monkeypatch.setattr(gate_mod, "has_unloaded_segments", lambda session_id: session_id == "sess_reload")
    result = asyncio.run(compression_gate_execute(hook_input))
    assert result.patched_payload is not None
    names = [tool.name for tool in result.patched_payload.tools]
    assert "reload_unloaded_segments" in names


def test_reload_unloaded_segments_restores_image_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _DummyMemory()
    context = _build_context(tmp_path, memory=memory)
    image_path = _make_sample_png(tmp_path, "reload.png")
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    from fairyclaw.capabilities.compression_hooks.scripts import _unloaded_segments_state as state_mod

    monkeypatch.setattr(state_mod, "resolve_memory_root", lambda mkdir=True: tmp_path)
    save_unloaded_segments_state(
        session_id="sess_test_image_ops",
        state={
            "records": [
                {
                    "unload_id": "imgu_restore",
                    "role": "user",
                    "segments": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}],
                    "restored": False,
                }
            ]
        },
        memory_root=str(tmp_path),
    )
    raw = asyncio.run(reload_execute({"mode": "latest"}, context))
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["restored_count"] == 1
    assert len(memory.events) == 1


def test_resolve_image_payload_fetches_https_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fairyclaw.capabilities.image_ops.scripts import _shared as sh

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z7xkAAAAASUVORK5CYII="
    )

    class _Resp:
        content = png
        headers = {"content-type": "image/png"}

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: object | None = None) -> _Resp:
            assert str(url).startswith("https://")
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    ctx = _build_context(tmp_path)

    async def run() -> object:
        return await sh.resolve_image_payload(
            ctx,
            input_ref={"url": "https://example.com/x.png"},
            max_bytes=1_000_000,
        )

    payload, err = asyncio.run(run())
    assert err is None
    assert payload is not None
    assert payload.raw_bytes == png


def test_resolve_image_payload_rejects_chat_style_file_id(tmp_path: Path) -> None:
    from fairyclaw.capabilities.image_ops.scripts import _shared as sh

    ctx = _build_context(tmp_path)

    async def run() -> object:
        return await sh.resolve_image_payload(ctx, input_ref={"file_id": "imgu_a18e6a032837"})

    payload, err = asyncio.run(run())
    assert payload is None
    assert err and "file_" in err

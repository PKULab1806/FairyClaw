# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from __future__ import annotations

import asyncio
import base64

import pytest

from fairyclaw.infrastructure.llm.client import OpenAICompatibleLLMClient
from fairyclaw.infrastructure.llm.config import LLMEndpointProfile

# 1×1 PNG (black-ish) — use as **edit input** in tests.
_INPUT_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO7Z7xkAAAAASUVORK5CYII="
)
# 1×1 PNG (different pixels) — use as **model output** so edit flow is not "identity".
_OUTPUT_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lV7yWQAAAABJRU5ErkJggg=="
)


class _FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": [{"b64_json": _OUTPUT_PNG_B64}]}


def test_generate_image_edit_uses_configured_multipart_image_field(monkeypatch) -> None:
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        api_path="/images/edits",
        model="image-edit-model-alpha",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="openai_images_edits_multipart",
        response_image_field="data.0.b64_json",
        multipart_image_field="image[]",
    )
    client = OpenAICompatibleLLMClient(profile)

    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["init"] = {"args": args, "kwargs": kwargs}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            captured["url"] = url
            captured["data"] = data
            captured["files"] = files
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    raw = base64.b64decode(_INPUT_PNG_B64)
    result = asyncio.run(
        client.generate_image_edit(
            prompt="replace background",
            input_image_bytes=raw,
            input_mime="image/png",
        )
    )
    assert result == base64.b64decode(_OUTPUT_PNG_B64)
    files = captured.get("files")
    assert isinstance(files, dict)
    assert "image[]" in files


def test_generate_image_edit_img_urls_format_posts_images_edits_json(monkeypatch) -> None:
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="vendor/image-edit-model-alpha",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"b64_json": _OUTPUT_PNG_B64}]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["init"] = {"args": args, "kwargs": kwargs}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    raw = base64.b64decode(_INPUT_PNG_B64)
    result = asyncio.run(
        client.generate_image_edit(
            prompt="make it blue",
            input_image_bytes=raw,
            input_mime="image/png",
        )
    )
    assert result == base64.b64decode(_OUTPUT_PNG_B64)
    assert captured.get("url") == "https://api.example.com/v1/images/edits"
    body = captured.get("json")
    assert isinstance(body, dict)
    assert body.get("model") == "vendor/image-edit-model-alpha"
    assert body.get("prompt") == "make it blue"
    iu = body.get("img_urls")
    assert isinstance(iu, list) and len(iu) == 1
    assert str(iu[0]).startswith("data:image/png;base64,")


def test_generate_image_edit_img_urls_format_text_only_uses_generations_json(monkeypatch) -> None:
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="vendor/image-edit-model-alpha",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"b64_json": _OUTPUT_PNG_B64}]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["init"] = {"args": args, "kwargs": kwargs}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    result = asyncio.run(client.generate_image_edit(prompt="a red dot on white"))
    assert result
    assert captured.get("url") == "https://api.example.com/v1/images/generations"
    body = captured.get("json")
    assert isinstance(body, dict)
    assert body.get("n") == 1
    assert body.get("response_format") == "b64_json"


def test_generate_image_edit_resolves_https_image_url_in_data_array(monkeypatch) -> None:
    """Some endpoints return ``data[0].url`` (HTTPS) instead of inline base64."""
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="test-image-model",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    tiny_png = base64.b64decode(_OUTPUT_PNG_B64)

    class _JsonResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"url": "https://cdn.example.com/out/output.PNG", "revised_prompt": ""}]}

    class _BytesResp:
        status_code = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        @property
        def content(self) -> bytes:
            return self._body

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            self._seen_get = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            return _JsonResp()

        async def get(self, url):
            self._seen_get = True
            assert "cdn.example.com" in str(url)
            return _BytesResp(tiny_png)

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    raw = base64.b64decode(_INPUT_PNG_B64)
    out = asyncio.run(
        client.generate_image_edit(
            prompt="edit",
            input_image_bytes=raw,
            input_mime="image/png",
        )
    )
    assert out == tiny_png


def test_generate_image_edit_decodes_data_url_embedded_in_assistant_text(monkeypatch) -> None:
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="test-image-model",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    out_b64 = _OUTPUT_PNG_B64

    class _JsonResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Here: data:image/png;base64,{out_b64} trailing",
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            return _JsonResp()

        async def get(self, url):
            raise AssertionError("GET should not be needed when data URL is embedded")

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    raw = base64.b64decode(_INPUT_PNG_B64)
    out = asyncio.run(
        client.generate_image_edit(
            prompt="edit",
            input_image_bytes=raw,
            input_mime="image/png",
        )
    )
    assert out == base64.b64decode(_OUTPUT_PNG_B64)


def test_generate_image_edit_decodes_b64_from_data_array(monkeypatch) -> None:
    """``images/edits``-style responses often use ``data[0].b64_json``."""
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="test-image-model",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    tiny_b64 = _OUTPUT_PNG_B64
    tiny_png = base64.b64decode(tiny_b64)
    input_png = base64.b64decode(_INPUT_PNG_B64)

    class _JsonResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"b64_json": tiny_b64, "revised_prompt": ""}]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            return _JsonResp()

        async def get(self, url):
            raise AssertionError("unexpected GET")

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(
        client.generate_image_edit(
            prompt="edit",
            input_image_bytes=input_png,
            input_mime="image/png",
        )
    )
    assert out == tiny_png


def test_generate_image_edit_skips_identity_then_uses_later_part_in_assistant_content(monkeypatch) -> None:
    """When ``choices`` message lists multiple image_url parts, skip bytes identical to input."""
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="vendor/image-edit-model-alpha",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    inp = base64.b64decode(_INPUT_PNG_B64)
    out_png = base64.b64decode(_OUTPUT_PNG_B64)
    url_in = f"data:image/png;base64,{_INPUT_PNG_B64}"
    url_out = f"data:image/png;base64,{_OUTPUT_PNG_B64}"

    class _JsonResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "image_url", "image_url": {"url": url_in}},
                                {"type": "image_url", "image_url": {"url": url_out}},
                            ],
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            return _JsonResp()

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    got = asyncio.run(
        client.generate_image_edit(prompt="edit stars", input_image_bytes=inp, input_mime="image/png")
    )
    assert got == out_png


def test_generate_image_edit_raises_when_only_identity_candidates(monkeypatch) -> None:
    """If the only returned image bytes match the edit input, treat as failure (echo)."""
    profile = LLMEndpointProfile(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="vendor/image-edit-model-alpha",
        api_key_env="TEST_IMAGE_KEY",
        profile_type="image_generation",
        request_format="images_edits_img_urls_json",
    )
    client = OpenAICompatibleLLMClient(profile)
    inp = base64.b64decode(_INPUT_PNG_B64)
    echo_b64 = _INPUT_PNG_B64

    class _JsonResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"b64_json": echo_b64}]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, data=None, files=None, headers=None, json=None):
            return _JsonResp()

    import httpx

    monkeypatch.setenv("TEST_IMAGE_KEY", "dummy-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(RuntimeError, match="usable edited|matched the input bytes"):
        asyncio.run(
            client.generate_image_edit(prompt="edit", input_image_bytes=inp, input_mime="image/png")
        )


def test_summarize_image_response_for_error_describes_assistant_message() -> None:
    profile = LLMEndpointProfile(
        name="main",
        api_base="https://api.example.com/v1",
        model="m",
        api_key_env="K",
    )
    client = OpenAICompatibleLLMClient(profile)
    hint = client._summarize_image_response_for_error(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "no image here"}],
                    }
                }
            ]
        }
    )
    assert "message.content_type=list" in hint
    assert "parts=" in hint

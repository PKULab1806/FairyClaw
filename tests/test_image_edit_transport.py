# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Contract tests for ``resolve_image_edit_transport`` (image API request shapes)."""

from __future__ import annotations

import base64

import pytest

from fairyclaw.infrastructure.llm.config import LLMEndpointProfile
from fairyclaw.infrastructure.llm.image_edit_transport import resolve_image_edit_transport

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z7xkAAAAASUVORK5CYII="
)


def _profile(**kwargs: object) -> LLMEndpointProfile:
    base = dict(
        name="image_generation",
        api_base="https://api.example.com/v1",
        model="test-model",
        api_key_env="TEST_KEY",
        profile_type="image_generation",
    )
    base.update(kwargs)
    return LLMEndpointProfile(**base)  # type: ignore[arg-type]


def test_images_edits_img_urls_json_with_image_posts_model_img_urls_prompt() -> None:
    p = _profile(request_format="images_edits_img_urls_json")
    t = resolve_image_edit_transport(
        p, prompt="edit sky", input_image_bytes=_TINY_PNG, input_mime="image/png", size=None
    )
    assert t.post_mode == "json"
    assert t.url == "https://api.example.com/v1/images/edits"
    assert t.json_body is not None
    assert t.json_body.get("model") == "test-model"
    assert t.json_body.get("prompt") == "edit sky"
    urls = t.json_body.get("img_urls")
    assert isinstance(urls, list) and len(urls) == 1
    assert str(urls[0]).startswith("data:image/png;base64,")


def test_images_edits_img_urls_json_text_only_always_generations_json() -> None:
    p = _profile(request_format="images_edits_img_urls_json")
    t = resolve_image_edit_transport(p, prompt="a red dot", input_image_bytes=None, input_mime="image/png", size=None)
    assert t.post_mode == "json"
    assert t.url == "https://api.example.com/v1/images/generations"
    assert t.json_body == {
        "model": "test-model",
        "prompt": "a red dot",
        "n": 1,
        "response_format": "b64_json",
    }


def test_openai_images_edits_json_switches_to_generations_without_image() -> None:
    p = _profile(request_format="openai_images_edits_json", api_path="/images/edits")
    t = resolve_image_edit_transport(p, prompt="x", input_image_bytes=None, input_mime="image/png", size=None)
    assert t.url.endswith("/images/generations")
    assert t.json_body == {"model": "test-model", "prompt": "x"}


def test_openai_images_edits_json_with_image_keeps_edits_and_data_url() -> None:
    p = _profile(request_format="openai_images_edits_json", api_path="/images/edits")
    t = resolve_image_edit_transport(p, prompt="x", input_image_bytes=_TINY_PNG, input_mime="image/png", size="1024x1024")
    assert t.url.endswith("/images/edits")
    assert t.json_body is not None
    assert t.json_body["model"] == "test-model"
    assert t.json_body["prompt"] == "x"
    assert t.json_body["size"] == "1024x1024"
    assert str(t.json_body["image"]).startswith("data:image/png;base64,")


def test_openai_images_edits_multipart_with_image() -> None:
    p = _profile(
        request_format="openai_images_edits_multipart",
        api_path="/images/edits",
        multipart_image_field="image[]",
    )
    t = resolve_image_edit_transport(p, prompt="x", input_image_bytes=_TINY_PNG, input_mime="image/png", size=None)
    assert t.post_mode == "multipart"
    assert t.url.endswith("/images/edits")
    assert t.form_fields is not None and t.multipart_files is not None
    assert "image[]" in t.multipart_files


def test_openai_images_edits_multipart_text_only_generations() -> None:
    p = _profile(request_format="openai_images_edits_multipart", api_path="/images/edits")
    t = resolve_image_edit_transport(p, prompt="y", input_image_bytes=None, input_mime="image/png", size=None)
    assert t.post_mode == "json"
    assert t.url.endswith("/images/generations")


def test_images_edits_img_urls_json_respects_custom_api_path() -> None:
    p = _profile(request_format="images_edits_img_urls_json", api_path="/custom/edits")
    t = resolve_image_edit_transport(
        p, prompt="x", input_image_bytes=_TINY_PNG, input_mime="image/png", size=None
    )
    assert t.url == "https://api.example.com/v1/custom/edits"


def test_unknown_request_format_raises() -> None:
    p = _profile(request_format="no_such_format")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="Unsupported"):
        resolve_image_edit_transport(p, prompt="z", input_image_bytes=None, input_mime="image/png", size=None)

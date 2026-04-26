# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Resolve HTTP URL and body for image generation / image-edit API calls.

Contract (unit-tested in ``tests/test_image_edit_transport.py``), consumed by
``OpenAICompatibleLLMClient.generate_image_edit``:

- ``images_edits_img_urls_json``: JSON to ``api_path`` (default ``images/edits``) with
  ``model``, ``img_urls`` (list of image references, typically one ``data:image/...;base64,...`` URL per
  local file), and ``prompt``. Text-only: POST ``{api_base}/images/generations`` with standard JSON.
- ``openai_images_edits_json``: JSON to ``api_path`` (default ``/images/edits``); if there is no
  input image and path ends with ``/images/edits``, callers use ``/images/generations``.
- ``openai_images_edits_multipart``: multipart to ``api_path`` when an input image exists; otherwise
  JSON to ``/images/generations``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Literal

from fairyclaw.infrastructure.llm.config import LLMEndpointProfile


@dataclass(frozen=True)
class ImageEditTransport:
    """Resolved single POST for ``generate_image_edit``."""

    url: str
    post_mode: Literal["json", "multipart"]
    json_body: dict[str, Any] | None
    form_fields: dict[str, str] | None
    multipart_files: dict[str, tuple[str, bytes, str]] | None


def _join_url(api_base: str, api_path: str) -> str:
    return f"{api_base.rstrip('/')}/{api_path.lstrip('/')}"


def _input_data_url(input_image_bytes: bytes, input_mime: str) -> str:
    return f"data:{input_mime};base64,{base64.b64encode(input_image_bytes).decode('utf-8')}"


def _generations_json_body(
    *,
    model: str,
    prompt: str,
    size: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": str(model),
        "prompt": str(prompt),
        "n": 1,
        "response_format": "b64_json",
    }
    if size:
        body["size"] = str(size)
    return body


def resolve_image_edit_transport(
    profile: LLMEndpointProfile,
    *,
    prompt: str,
    input_image_bytes: bytes | None,
    input_mime: str,
    size: str | None,
) -> ImageEditTransport:
    """Map profile + payload to one HTTP POST (json or multipart)."""
    has_input = bool(input_image_bytes)
    raw_format = str(profile.request_format or "openai_images_edits_json").strip()
    if raw_format not in (
        "openai_images_edits_json",
        "openai_images_edits_multipart",
        "images_edits_img_urls_json",
    ):
        raise RuntimeError(f"Unsupported image request_format: {raw_format}")
    fmt = raw_format

    api_base = str(profile.api_base).rstrip("/")
    model = str(profile.model)
    multipart_field = str(profile.multipart_image_field or "image").strip() or "image"

    if fmt == "images_edits_img_urls_json":
        if has_input:
            assert input_image_bytes is not None
            path = str(profile.api_path or "images/edits").lstrip("/")
            data_url = _input_data_url(input_image_bytes, input_mime)
            body: dict[str, Any] = {
                "model": model,
                "img_urls": [data_url],
                "prompt": str(prompt),
            }
            if size:
                body["size"] = str(size)
            return ImageEditTransport(
                url=_join_url(api_base, path),
                post_mode="json",
                json_body=body,
                form_fields=None,
                multipart_files=None,
            )
        return ImageEditTransport(
            url=_join_url(api_base, "images/generations"),
            post_mode="json",
            json_body=_generations_json_body(model=model, prompt=prompt, size=size),
            form_fields=None,
            multipart_files=None,
        )

    # openai_images_edits_* : shared default path
    api_path = str(profile.api_path or "/images/edits").lstrip("/")
    if not has_input and api_path.rstrip("/").endswith("images/edits"):
        api_path = "images/generations"
    url = _join_url(api_base, api_path)

    if fmt == "openai_images_edits_json":
        body_json: dict[str, Any] = {"model": model, "prompt": prompt}
        if has_input:
            assert input_image_bytes is not None
            body_json["image"] = _input_data_url(input_image_bytes, input_mime)
        if size:
            body_json["size"] = size
        return ImageEditTransport(
            url=url,
            post_mode="json",
            json_body=body_json,
            form_fields=None,
            multipart_files=None,
        )

    # openai_images_edits_multipart
    if has_input:
        assert input_image_bytes is not None
        file_ext = "png"
        if "/" in input_mime:
            maybe = input_mime.split("/", 1)[1].lower().strip()
            if maybe:
                file_ext = maybe
        data: dict[str, str] = {"model": model, "prompt": str(prompt)}
        if size:
            data["size"] = str(size)
        files = {
            multipart_field: (f"input.{file_ext}", input_image_bytes, input_mime),
        }
        return ImageEditTransport(
            url=url,
            post_mode="multipart",
            json_body=None,
            form_fields=data,
            multipart_files=files,
        )
    return ImageEditTransport(
        url=url,
        post_mode="json",
        json_body=_generations_json_body(model=model, prompt=prompt, size=size),
        form_fields=None,
        multipart_files=None,
    )

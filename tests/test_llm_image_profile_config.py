# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from __future__ import annotations

from pathlib import Path

from fairyclaw.config.loader import save_yaml_atomic
from fairyclaw.config.settings import settings
from fairyclaw.infrastructure.llm.config import load_llm_endpoint_config


def test_llm_profile_loader_supports_optional_image_fields(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "llm_endpoints.yaml"
    save_yaml_atomic(
        cfg_path,
        {
            "default_profile": "main",
            "profiles": {
                "main": {
                    "api_base": "https://api.example.com/v1",
                    "model": "gpt-test",
                    "api_key_env": "OPENAI_API_KEY",
                },
                "image_generation": {
                    "type": "image_generation",
                    "api_base": "https://api.example.com/v1beta/openai",
                    "api_path": "/images/edits",
                    "model": "image-edit-model-alpha",
                    "api_key_env": "IMAGE_VENDOR_API_KEY",
                    "response_image_field": "data.0.b64_json",
                    "request_format": "openai_images_edits_json",
                    "multipart_image_field": "image[]",
                },
            },
        },
    )
    monkeypatch.setattr(settings, "llm_endpoints_config_path", str(cfg_path))

    loaded = load_llm_endpoint_config()
    image = loaded.profiles["image_generation"]
    assert image.profile_type == "image_generation"
    assert image.api_path == "/images/edits"
    assert image.response_image_field == "data.0.b64_json"
    assert image.request_format == "openai_images_edits_json"
    assert image.multipart_image_field == "image[]"

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""LLM endpoint configuration loader."""

from dataclasses import dataclass
from pathlib import Path

from typing import Any

from fairyclaw.config.loader import load_yaml, save_yaml_atomic
from fairyclaw.config.settings import settings


@dataclass
class LLMEndpointProfile:
    """Describe one model endpoint profile.

    Attributes:
        name (str): Profile name key.
        api_base (str): Base URL of OpenAI-compatible endpoint.
        model (str): Model identifier.
        api_key_env (str): Environment variable name holding API key.
        timeout_seconds (int): Request timeout in seconds.
        temperature (float): Sampling temperature used for chat requests.
    """

    name: str
    api_base: str
    model: str
    api_key_env: str
    timeout_seconds: int = 30
    temperature: float = 0.2


@dataclass
class LLMEndpointConfig:
    """Represent loaded LLM endpoint configuration.

    Attributes:
        default_profile (str): Default profile name.
        fallback_profile (str | None): Optional fallback profile name.
        profiles (dict[str, LLMEndpointProfile]): Profile mapping by name.
    """

    default_profile: str
    fallback_profile: str | None
    profiles: dict[str, LLMEndpointProfile]


def load_llm_endpoint_config() -> LLMEndpointConfig:
    """Load LLM endpoint configuration from YAML file.

    Returns:
        LLMEndpointConfig: Parsed endpoint configuration with defensive defaults.
    """
    path = Path(settings.llm_endpoints_config_path)
    if not path.exists():
        return LLMEndpointConfig(default_profile="main", fallback_profile=None, profiles={})
    data = load_yaml(path)
    default_profile = data.get("default_profile", "main")
    fallback_profile_raw = data.get("fallback_profile")
    fallback_profile = str(fallback_profile_raw).strip() if isinstance(fallback_profile_raw, str) and fallback_profile_raw.strip() else None
    raw_profiles = data.get("profiles", {}) or {}
    profiles: dict[str, LLMEndpointProfile] = {}
    for name, raw in raw_profiles.items():
        profiles[name] = LLMEndpointProfile(
            name=name,
            api_base=str(raw.get("api_base", "https://api.openai.com/v1")),
            model=str(raw.get("model", "gpt-4o-mini")),
            api_key_env=str(raw.get("api_key_env", "OPENAI_API_KEY")),
            timeout_seconds=int(raw.get("timeout_seconds", 30)),
            temperature=float(raw.get("temperature", 0.2)),
        )
    return LLMEndpointConfig(default_profile=default_profile, fallback_profile=fallback_profile, profiles=profiles)


def get_llm_document() -> dict[str, Any]:
    """Return the current ``llm_endpoints.yaml`` tree as a JSON-compatible dict."""
    path = Path(settings.llm_endpoints_config_path)
    if not path.exists():
        return {"default_profile": "main", "fallback_profile": None, "profiles": {}}
    return load_yaml(path)


def apply_llm_document(document: dict[str, Any]) -> None:
    """Persist LLM endpoints YAML and rely on the next ``create_*_llm_client`` load to pick it up."""
    if not isinstance(document.get("profiles"), dict):
        raise ValueError("document.profiles must be an object")
    dp = document.get("default_profile")
    if not isinstance(dp, str) or not dp.strip():
        raise ValueError("document.default_profile must be a non-empty string")
    save_yaml_atomic(settings.llm_endpoints_config_path, document)

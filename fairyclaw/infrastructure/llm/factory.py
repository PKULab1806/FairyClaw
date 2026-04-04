# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""LLM client factory."""

from fairyclaw.infrastructure.llm.client import OpenAICompatibleLLMClient
from fairyclaw.infrastructure.llm.config import load_llm_endpoint_config


def _resolve_fallback_profile(cfg, current_profile_name: str):
    """Resolve fallback profile object for a given active profile.

    Args:
        cfg: Loaded LLM endpoint config object.
        current_profile_name (str): Active profile name.

    Returns:
        LLMEndpointProfile | None: Fallback profile object or None.
    """
    fallback_name = cfg.fallback_profile
    if not fallback_name or fallback_name == current_profile_name:
        return None
    return cfg.profiles.get(fallback_name)


def create_default_llm_client() -> OpenAICompatibleLLMClient:
    """Create LLM client using configured default profile.

    Returns:
        OpenAICompatibleLLMClient: Initialized client with optional fallback profile.

    Raises:
        RuntimeError: Raised when default profile is missing in configuration.
    """
    cfg = load_llm_endpoint_config()
    profile = cfg.profiles.get(cfg.default_profile)
    if not profile:
        raise RuntimeError(f"Could not create LLM client. Profile '{cfg.default_profile}' not found in config. Available profiles: {list(cfg.profiles.keys())}")
    fallback_profile = _resolve_fallback_profile(cfg, cfg.default_profile)
    return OpenAICompatibleLLMClient(profile, fallback_profile=fallback_profile)

def create_llm_client(profile_name: str) -> OpenAICompatibleLLMClient:
    """Create LLM client by explicit profile name.

    Args:
        profile_name (str): Target profile name.

    Returns:
        OpenAICompatibleLLMClient: Initialized client with optional fallback profile.

    Raises:
        RuntimeError: Raised when requested profile is missing in configuration.
    """
    cfg = load_llm_endpoint_config()
    profile = cfg.profiles.get(profile_name)
    if not profile:
        raise RuntimeError(f"Could not create LLM client. Profile '{profile_name}' not found in config. Available profiles: {list(cfg.profiles.keys())}")
    fallback_profile = _resolve_fallback_profile(cfg, profile_name)
    return OpenAICompatibleLLMClient(profile, fallback_profile=fallback_profile)

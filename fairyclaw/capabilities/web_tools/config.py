# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime configuration model for the web_tools capability group."""

from pydantic import BaseModel


class WebToolsRuntimeConfig(BaseModel):
    """Frozen runtime configuration for web_tools capability scripts."""

    model_config = {"frozen": True}

    web_proxy: str | None = None


runtime_config_model = WebToolsRuntimeConfig

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime configuration model for the sourced_research capability group."""

from pydantic import BaseModel


class SourcedResearchRuntimeConfig(BaseModel):
    """Frozen runtime configuration for sourced_research capability scripts."""

    model_config = {"frozen": True}

    web_proxy: str | None = None


runtime_config_model = SourcedResearchRuntimeConfig

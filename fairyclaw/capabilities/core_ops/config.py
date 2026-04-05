# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime configuration model for the core_ops capability group."""

from pydantic import BaseModel


class CoreOpsRuntimeConfig(BaseModel):
    """Frozen runtime configuration for core_ops capability scripts."""

    model_config = {"frozen": True}

    execution_timeout_seconds: int = 30


runtime_config_model = CoreOpsRuntimeConfig

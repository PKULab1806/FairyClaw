# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Configuration loading helpers."""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load YAML file into dictionary with empty-object fallback.

    Args:
        path (str | Path): YAML file path.

    Returns:
        dict[str, Any]: Parsed YAML mapping; empty dict when file is empty.
    """
    raw = Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(raw) or {}

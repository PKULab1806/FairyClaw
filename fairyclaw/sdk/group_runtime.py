# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""SDK group-level runtime configuration loader.

Each capability group defines its own Pydantic ``BaseModel`` (frozen recommended)
to hold group-specific runtime parameters.  This module provides the unified
loader that materialises those models once at process startup and the helper
that capability scripts use to retrieve the typed snapshot from ``ToolContext``.

Usage in a group-config model file::

    from pydantic import BaseModel

    class WebToolsRuntimeConfig(BaseModel):
        model_config = {"frozen": True}
        web_proxy: str | None = None

Usage in a capability script::

    from fairyclaw.sdk.group_runtime import expect_group_config
    from my_group.config import WebToolsRuntimeConfig

    async def execute(args, ctx):
        cfg = expect_group_config(ctx, WebToolsRuntimeConfig)
        proxy = cfg.web_proxy

Usage in the registry / startup (called once per group)::

    from fairyclaw.sdk.group_runtime import load_group_runtime_config
    config = load_group_runtime_config(
        group_name="web_tools",
        group_dir=Path("fairyclaw/capabilities/web_tools"),
        model=WebToolsRuntimeConfig,
        core_settings=settings,
    )
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_LEGACY_KEY_MAP: dict[str, dict[str, str]] = {
    "web_tools": {"FAIRYCLAW_WEB_PROXY": "web_proxy"},
    "sourced_research": {"FAIRYCLAW_WEB_PROXY": "web_proxy"},
    "core_ops": {"FAIRYCLAW_EXECUTION_TIMEOUT_SECONDS": "execution_timeout_seconds"},
}


def _load_yaml_optional(path: Path) -> dict[str, object]:
    """Load YAML file if it exists; return empty dict otherwise."""
    if not path.exists():
        return {}
    try:
        from fairyclaw.config.loader import load_yaml  # lazy import

        return load_yaml(path)
    except Exception as exc:
        logger.warning("group_runtime: failed to load %s: %s", path, exc)
        return {}


def _collect_env_overrides(group_name: str) -> dict[str, object]:
    """Collect ``FAIRYCLAW_CAP_<GROUP>__<FIELD>`` env vars into a flat dict.

    Also maps deprecated flat keys (e.g. ``FAIRYCLAW_WEB_PROXY``) for the
    transition period; these take lower precedence than the new-style keys.
    """
    prefix = f"FAIRYCLAW_CAP_{group_name.upper()}__"
    result: dict[str, object] = {}

    # Deprecated legacy flat keys (lower precedence)
    for legacy_key, field_name in _LEGACY_KEY_MAP.get(group_name, {}).items():
        value = os.environ.get(legacy_key)
        if value is not None:
            result[field_name] = value

    # New-style prefixed keys (higher precedence, override legacy)
    for key, value in os.environ.items():
        if key.startswith(prefix):
            field_name = key[len(prefix):].lower()
            result[field_name] = value

    return result


def load_group_runtime_config(
    *,
    group_name: str,
    group_dir: Path,
    model: type[T],
    core_settings: object | None = None,
) -> T:
    """Load and materialise a frozen group runtime config snapshot.

    Data sources are merged in ascending precedence order:

    1. ``<group_dir>/config.yaml`` (static defaults for structured/large config)
    2. ``FAIRYCLAW_CAP_<GROUP>__<FIELD>`` environment variables
    3. Deprecated flat ``FAIRYCLAW_*`` keys (transition period, lower priority
       than new-style keys but higher than YAML)

    If ``core_settings`` is provided it is used only to derive absolute paths
    (e.g. ``data_dir``) that the loader may inject into the model as convenience
    fields — the whole settings object is never forwarded to capability scripts.

    Args:
        group_name: Capability group identifier (snake_case, matches directory name).
        group_dir: Absolute path to the capability group directory.
        model: Pydantic ``BaseModel`` subclass that defines the group's config schema.
        core_settings: Optional core ``Settings`` instance for path derivation.

    Returns:
        A validated, frozen (if ``model_config["frozen"] = True``) instance of ``model``.
    """
    yaml_data = _load_yaml_optional(group_dir / "config.yaml")
    env_overrides = _collect_env_overrides(group_name)

    merged: dict[str, object] = {}
    merged.update(yaml_data)
    merged.update(env_overrides)

    # Optionally inject derived paths from core settings without exposing the whole object
    if core_settings is not None:
        data_dir = getattr(core_settings, "data_dir", None)
        if data_dir is not None and "data_dir" not in merged:
            merged.setdefault("data_dir", data_dir)

    return model.model_validate(merged)


def expect_group_config(ctx: object, model: type[T]) -> T:
    """Retrieve and type-check the group runtime config snapshot from a context.

    Intended for use inside tool and hook executor functions.  Raises
    ``TypeError`` when the snapshot is absent or is an unexpected type, giving
    a clear error message rather than a confusing ``AttributeError``.

    Args:
        ctx: A ``ToolContext`` or hook execution context carrying
            ``group_runtime_config``.
        model: Expected ``BaseModel`` subclass.

    Returns:
        The typed group config snapshot.

    Raises:
        TypeError: When ``group_runtime_config`` is missing or the wrong type.
    """
    config = getattr(ctx, "group_runtime_config", None)
    if config is None:
        raise TypeError(
            f"expect_group_config: no group_runtime_config injected into context "
            f"({type(ctx).__name__}); ensure the group defines a runtime config model "
            f"and the registry loads it."
        )
    if not isinstance(config, model):
        raise TypeError(
            f"expect_group_config: expected {model.__name__}, "
            f"got {type(config).__name__}"
        )
    return config


__all__ = [
    "expect_group_config",
    "load_group_runtime_config",
]

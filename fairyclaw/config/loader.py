# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Configuration loading helpers."""

from __future__ import annotations

import json
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


def save_json_atomic(path: str | Path, data: dict[str, Any]) -> None:
    """Atomically write a JSON object (UTF-8, indent 2)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def save_yaml_atomic(path: str | Path, data: dict[str, Any]) -> None:
    """Atomically write YAML mapping (temp file + replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a ``.env``-style file."""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        k = key.strip()
        if not k:
            continue
        v = val.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
            v = v[1:-1]
        out[k] = v
    return out


def write_env_file_atomic(path: str | Path, mapping: dict[str, str]) -> None:
    """Write env file atomically (sorted keys for stability)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(mapping.items())]
    text = "\n".join(lines) + ("\n" if lines else "")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def merge_env_keys(path: str | Path, updates: dict[str, str]) -> None:
    """Merge string updates into an env file; other keys are preserved."""
    base = read_env_file(path)
    base.update(updates)
    write_env_file_atomic(path, base)


def merge_whitelisted_env(
    path: str | Path,
    updates: dict[str, str],
    *,
    whitelist: frozenset[str],
) -> None:
    """Merge only whitelisted keys into the env file."""
    filtered = {k: v for k, v in updates.items() if k in whitelist}
    if not filtered:
        return
    base = read_env_file(path)
    for k, v in filtered.items():
        base[k] = v
    write_env_file_atomic(path, base)

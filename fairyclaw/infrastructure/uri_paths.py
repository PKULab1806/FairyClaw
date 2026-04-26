# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Normalize URI-like strings to paths used by server-side file checks."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_file_uri_to_path(s: str) -> str:
    """Turn ``file:///abs/path`` (or ``file:/abs/path``) into ``/abs/path``.

    Models often emit RFC ``file:`` URLs; ``os.path.exists`` and path validators expect a plain path.
    Non-``file:`` strings are returned unchanged (aside from ``strip()``).
    """
    t = (s or "").strip()
    if not t.lower().startswith("file:"):
        return t
    path = urlparse(t).path
    return path if path else t

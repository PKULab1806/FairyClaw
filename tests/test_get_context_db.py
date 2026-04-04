# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for get_context_db unwrapping memory wrappers (e.g. BridgeOutputMemory)."""

from fairyclaw.core.capabilities.models import ToolContext, get_context_db


class _Repo:
    def __init__(self, db: object) -> None:
        self.db = db


class _InnerMemory:
    def __init__(self, db: object) -> None:
        self.repo = _Repo(db)


class _Wrapper:
    def __init__(self, base: object) -> None:
        self._base = base


def test_get_context_db_unwraps_base_chain() -> None:
    db_obj = object()
    inner = _InnerMemory(db_obj)
    wrapped = _Wrapper(inner)
    ctx = ToolContext(session_id="s1", memory=wrapped)
    db, err = get_context_db(ctx)
    assert err is None
    assert db is db_obj


def test_get_context_db_direct_memory() -> None:
    db_obj = object()
    ctx = ToolContext(session_id="s1", memory=_InnerMemory(db_obj))
    db, err = get_context_db(ctx)
    assert err is None
    assert db is db_obj

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""File ID lookup normalizes file_ + hex to lowercase (DB stores uuid4.hex)."""

from fairyclaw.infrastructure.database.repository import _normalize_file_id_for_lookup


def test_normalize_file_id_lowercases_hex_suffix() -> None:
    assert _normalize_file_id_for_lookup("file_ABCDEF0123456789ABCDEF01234567") == (
        "file_abcdef0123456789abcdef01234567"
    )


def test_normalize_file_id_unchanged_when_already_lower() -> None:
    s = "file_abcdef0123456789abcdef01234567"
    assert _normalize_file_id_for_lookup(s) == s

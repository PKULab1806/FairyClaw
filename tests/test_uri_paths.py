# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.infrastructure.uri_paths import normalize_file_uri_to_path


def test_normalize_file_uri_three_slashes() -> None:
    assert normalize_file_uri_to_path("file:///home/user/a.jpg") == "/home/user/a.jpg"


def test_normalize_plain_path_unchanged() -> None:
    assert normalize_file_uri_to_path("/home/user/a.jpg") == "/home/user/a.jpg"

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for embedding backends (no sentence-transformers in default install)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from fairyclaw.infrastructure.embedding import service as embedding_service_module
from fairyclaw.infrastructure.embedding.service import (
    EmbeddingProfile,
    OpenAICompatibleEmbedding,
    create_embedding_service,
)


def test_create_embedding_service_hashing_via_stub_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repo embedding profile may be API-based; test hashing branch with a stub profile."""

    def _stub_load(name: str) -> EmbeddingProfile:
        return EmbeddingProfile(
            name=name,
            model="hashing-test",
            backend="hashing",
            dimensions=384,
            profile_type="embedding",
        )

    monkeypatch.setattr(embedding_service_module, "load_embedding_profile", _stub_load)
    svc = create_embedding_service("embedding")
    out = asyncio.run(svc.embed(["ping"]))
    assert len(out) == 1
    assert len(out[0]) == 384


def test_openai_compatible_embedding_parses_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = EmbeddingProfile(
        name="e",
        model="text-embedding-test",
        backend="openai_compatible",
        dimensions=2,
        profile_type="embedding",
        api_base="https://example.invalid/v1",
        api_key_env="FAIRYCLAW_TEST_EMBED_KEY",
        timeout_seconds=30,
    )
    monkeypatch.setenv("FAIRYCLAW_TEST_EMBED_KEY", "sk-test")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"embedding": [0.0, 1.0], "index": 1},
            {"embedding": [1.0, 0.0], "index": 0},
        ]
    }

    class MockClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> MockClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, json: object = None, headers: object = None) -> MagicMock:
            assert url.endswith("/embeddings")
            return mock_response

    monkeypatch.setattr("fairyclaw.infrastructure.embedding.service.httpx.AsyncClient", MockClient)

    svc = OpenAICompatibleEmbedding(profile)
    out = asyncio.run(svc.embed(["first", "second"]))
    assert out[0] == [1.0, 0.0]
    assert out[1] == [0.0, 1.0]

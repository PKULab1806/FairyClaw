# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Embedding service backed by model profiles in llm_endpoints.yaml."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from fairyclaw.config.loader import load_yaml
from fairyclaw.config.settings import settings


@dataclass(frozen=True)
class EmbeddingProfile:
    """Embedding model profile loaded from llm_endpoints.yaml."""

    name: str
    model: str
    backend: str = "hashing"
    dimensions: int | None = None
    profile_type: str = "embedding"
    api_base: str | None = None
    api_key_env: str | None = None
    timeout_seconds: int = 60


class EmbeddingService(ABC):
    """Abstract embedding service."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        raise NotImplementedError


class SentenceTransformerEmbedding(EmbeddingService):
    """Sentence-transformers embedding service with lazy model loading."""

    _MODEL_CACHE: dict[str, object] = {}
    _CACHE_LOCK = Lock()

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def _get_model(self):  # type: ignore[no-untyped-def]
        with self._CACHE_LOCK:
            cached_model = self._MODEL_CACHE.get(self.model_name)
            if cached_model is not None:
                return cached_model
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError(
                "sentence-transformers is required for the embedding service. "
                "Install the optional dependency before enabling memory hooks."
            ) from exc
        model = SentenceTransformer(self.model_name)
        with self._CACHE_LOCK:
            self._MODEL_CACHE[self.model_name] = model
        return model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts and return dense vectors."""
        if not texts:
            return []
        model = self._get_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


class HashingEmbedding(EmbeddingService):
    """Fully local hashing-based embedding for offline MVP usage."""

    def __init__(self, model_name: str, dimensions: int = 384) -> None:
        self.model_name = model_name
        self.dimensions = max(32, dimensions)

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token for token in text.lower().split() if token]
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(f"{self.model_name}:{token}".encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts into normalized hashed bag-of-token vectors."""
        return [self._embed_one(text) for text in texts]


class OpenAICompatibleEmbedding(EmbeddingService):
    """OpenAI-compatible POST {api_base}/embeddings (no local torch)."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        if not profile.api_base or not str(profile.api_base).strip():
            raise RuntimeError(
                f"Embedding profile '{profile.name}' (backend openai_compatible) requires api_base in llm_endpoints.yaml."
            )
        if not profile.api_key_env or not str(profile.api_key_env).strip():
            raise RuntimeError(
                f"Embedding profile '{profile.name}' (backend openai_compatible) requires api_key_env in llm_endpoints.yaml."
            )
        self._profile = profile

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        api_key = os.getenv(self._profile.api_key_env or "", "").strip()
        if not api_key:
            raise RuntimeError(f"Missing API key env: {self._profile.api_key_env}")
        base = str(self._profile.api_base).rstrip("/")
        url = f"{base}/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self._profile.timeout_seconds)
        out: list[list[float] | None] = [None] * len(texts)
        batch_size = 32
        async with httpx.AsyncClient(timeout=timeout) as client:
            for start in range(0, len(texts), batch_size):
                chunk = texts[start : start + batch_size]
                payload: dict[str, Any] = {"model": self._profile.model, "input": chunk}
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                rows = data.get("data", []) if isinstance(data, dict) else []
                if len(rows) != len(chunk):
                    raise RuntimeError(
                        f"Embeddings API returned {len(rows)} rows for {len(chunk)} inputs "
                        f"(profile={self._profile.name})."
                    )
                ordered: list[dict[str, Any]] = [r for r in rows if isinstance(r, dict)]
                ordered.sort(key=lambda r: int(r.get("index", 0)))
                for item in ordered:
                    emb = item.get("embedding")
                    if not isinstance(emb, list):
                        raise RuntimeError(
                            f"Embeddings API missing embedding list (profile={self._profile.name})."
                        )
                    idx_in_batch = int(item.get("index", 0))
                    global_i = start + idx_in_batch
                    if not (0 <= global_i < len(out)):
                        raise RuntimeError(
                            f"Embeddings API index out of range (profile={self._profile.name})."
                        )
                    out[global_i] = [float(x) for x in emb]
        if any(v is None for v in out):
            raise RuntimeError(f"Embeddings API missing vectors (profile={self._profile.name}).")
        return [v for v in out if v is not None]


def load_embedding_profile(profile_name: str) -> EmbeddingProfile:
    """Load one embedding profile from llm_endpoints.yaml."""
    path = Path(settings.llm_endpoints_config_path)
    if not path.exists():
        raise RuntimeError(f"LLM endpoint config not found: {path}")
    data = load_yaml(path)
    raw_profiles = data.get("profiles", {}) or {}
    raw = raw_profiles.get(profile_name)
    if not isinstance(raw, dict):
        raise RuntimeError(f"Embedding profile '{profile_name}' not found in {path}")
    profile_type = str(raw.get("type", "chat"))
    if profile_type != "embedding":
        raise RuntimeError(f"Profile '{profile_name}' is not an embedding profile.")
    api_base_raw = raw.get("api_base")
    api_key_raw = raw.get("api_key_env")
    return EmbeddingProfile(
        name=profile_name,
        model=str(raw.get("model", "")),
        backend=str(raw.get("backend", "hashing")),
        dimensions=int(raw["dimensions"]) if raw.get("dimensions") is not None else None,
        profile_type=profile_type,
        api_base=str(api_base_raw).strip() if api_base_raw else None,
        api_key_env=str(api_key_raw).strip() if api_key_raw else None,
        timeout_seconds=int(raw.get("timeout_seconds", 60)),
    )


def create_embedding_service(profile_name: str) -> EmbeddingService:
    """Create an embedding service from llm_endpoints.yaml."""
    profile = load_embedding_profile(profile_name)
    if not profile.model:
        raise RuntimeError(f"Embedding profile '{profile_name}' is missing model.")
    normalized = profile.backend.replace("-", "_")
    if normalized == "sentence_transformers":
        return SentenceTransformerEmbedding(model_name=profile.model)
    if profile.backend == "hashing":
        return HashingEmbedding(model_name=profile.model, dimensions=profile.dimensions or 384)
    if profile.backend in ("openai_compatible", "http", "api"):
        return OpenAICompatibleEmbedding(profile)
    raise RuntimeError(f"Unsupported embedding backend: {profile.backend}")

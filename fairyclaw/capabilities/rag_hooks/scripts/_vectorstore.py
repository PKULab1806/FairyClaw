# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Lightweight Qdrant helper local to the RAG capability group."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5
import re

from fairyclaw.config.settings import settings

logger = logging.getLogger(__name__)


class LocalVectorStore:
    """Minimal local Qdrant wrapper for memory retrieval."""

    @staticmethod
    def _resolve_storage_path(storage_path: str) -> Path:
        path = Path(storage_path)
        if path.is_absolute():
            return path
        project_root = Path(settings.data_dir).resolve().parent
        return (project_root / path).resolve()

    def __init__(self, storage_path: str, collection_name: str) -> None:
        self.storage_path = self._resolve_storage_path(storage_path)
        self.collection_name = collection_name
        self._client = None
        self._models = None

    def _ensure_collection(self, client, models, vector_size: int) -> None:  # type: ignore[no-untyped-def]
        try:
            client.get_collection(self.collection_name)
            return
        except Exception:
            pass
        client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )

    def _get_client_and_models(self):  # type: ignore[no-untyped-def]
        if self._client is not None and self._models is not None:
            return self._client, self._models
        try:
            from qdrant_client import QdrantClient, models
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "qdrant-client is required for RAG retrieval. Install the optional dependency first."
            ) from exc
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(self.storage_path))
        self._models = models
        return self._client, self._models

    @staticmethod
    def _normalize_point_id(point_id: object) -> str:
        """Convert arbitrary business IDs into stable UUID strings."""
        return str(uuid5(NAMESPACE_URL, str(point_id)))

    @staticmethod
    def _build_payload(point: dict[str, Any]) -> dict[str, Any]:
        """Preserve the original business identifier inside payload."""
        payload = dict(point.get("payload", {}) or {})
        payload.setdefault("point_id", str(point.get("id", "")))
        return payload

    def upsert(self, points: list[dict[str, Any]], vector_size: int) -> None:
        """Insert or update vector points."""
        if not points:
            return
        client, models = self._get_client_and_models()
        self._ensure_collection(client, models, vector_size)
        client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=self._normalize_point_id(point["id"]),
                    vector=point["vector"],
                    payload=self._build_payload(point),
                )
                for point in points
            ],
        )

    @staticmethod
    def _rerank_score(query_text: str, payload: dict[str, Any], vector_score: float) -> float:
        """Apply lightweight lexical reranking on top of vector similarity."""
        text = str(payload.get("text", "")).lower()
        category = str(payload.get("category", "")).lower()
        query = query_text.lower()
        score = float(vector_score)
        if category == "fact":
            score += 0.05
        if "secret" in query and "secret" in text:
            score += 0.10
        if "marker" in query and "marker" in text:
            score += 0.10
        if "path" in query and "/" in text:
            score += 0.08
        if ("url" in query or "docs" in query) and "http" in text:
            score += 0.08
        if "remember" in query and "remember" in text:
            score += 0.04
        overlap = LocalVectorStore._token_overlap(query, text)
        score += overlap * 0.08
        if "lorem ipsum" in text or "noise round" in text:
            score -= 0.25
        if "i don't have" in text or "no such information" in text:
            score -= 0.20
        return score

    def search(
        self,
        query_vector: list[float],
        limit: int,
        session_scope: list[str],
        score_threshold: float,
        query_text: str = "",
    ) -> list[dict[str, Any]]:
        """Search vectors and filter by session scope."""
        if not query_vector:
            return []
        client, _ = self._get_client_and_models()
        try:
            response = client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=max(limit * 5, limit),
                with_payload=True,
                with_vectors=False,
                score_threshold=score_threshold if score_threshold > 0 else None,
            )
        except ValueError as exc:
            # Local Qdrant: collection is created on first upsert; first retrieval is expected empty.
            err = str(exc).lower()
            if "collection" in err and "not found" in err:
                logger.debug(
                    "RAG LocalVectorStore: collection %r not created yet, returning no hits",
                    self.collection_name,
                )
                return []
            logger.warning(
                "RAG LocalVectorStore.query_points failed: collection=%s path=%s error=%s",
                self.collection_name,
                self.storage_path,
                exc,
                exc_info=True,
            )
            return []
        except Exception as exc:
            logger.warning(
                "RAG LocalVectorStore.query_points failed: collection=%s path=%s error=%s",
                self.collection_name,
                self.storage_path,
                exc,
                exc_info=True,
            )
            return []
        hits = list(getattr(response, "points", []) or [])
        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = dict(getattr(hit, "payload", {}) or {})
            session_id = str(payload.get("session_id", ""))
            if session_scope and session_id not in session_scope:
                continue
            score = float(getattr(hit, "score", 0.0))
            if score < score_threshold:
                continue
            results.append(
                {
                    "id": str(payload.get("point_id") or getattr(hit, "id", "")),
                    "score": score,
                    "rerank_score": self._rerank_score(query_text=query_text, payload=payload, vector_score=score),
                    "payload": payload,
                }
            )
        results.sort(key=lambda item: float(item.get("rerank_score", 0.0)), reverse=True)
        return results[:limit]

    @staticmethod
    def _token_overlap(query: str, text: str) -> int:
        """Count overlapping informative tokens."""
        token_pattern = re.compile(r"[a-z0-9][a-z0-9_\\-]{3,}")
        query_tokens = set(token_pattern.findall(query))
        text_tokens = set(token_pattern.findall(text))
        stopwords = {"what", "should", "reply", "only", "with", "this", "that", "later", "have", "your", "remember"}
        return len((query_tokens - stopwords) & (text_tokens - stopwords))

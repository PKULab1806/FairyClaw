# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Lightweight Qdrant helper local to the memory capability group."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fairyclaw.config.settings import settings

logger = logging.getLogger(__name__)


class LocalVectorStore:
    """Minimal local Qdrant wrapper for memory extraction."""

    def __init__(self, storage_path: str, collection_name: str) -> None:
        self.storage_path = self._resolve_storage_path(storage_path)
        self.collection_name = collection_name
        self._client = None
        self._models = None

    def search(self, query_vector: list[float], limit: int = 3, filter_payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Search points using vector similarity (qdrant-client 1.7+ uses query_points, not search)."""
        client, models = self._get_client_and_models()

        query_filter = None
        if filter_payload:
            must_conditions = [
                models.FieldCondition(key=k, match=models.MatchValue(value=v))
                for k, v in filter_payload.items()
            ]
            query_filter = models.Filter(must=must_conditions)

        try:
            response = client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            hits = list(getattr(response, "points", []) or [])
            out: list[dict[str, Any]] = []
            for hit in hits:
                payload = getattr(hit, "payload", None)
                out.append(
                    {
                        "id": getattr(hit, "id", ""),
                        "score": float(getattr(hit, "score", 0.0)),
                        "payload": dict(payload or {}),
                    }
                )
            return out
        except ValueError as exc:
            err = str(exc).lower()
            if "collection" in err and "not found" in err:
                logger.debug(
                    "LocalVectorStore.search: collection %r not created yet, returning no hits",
                    self.collection_name,
                )
                return []
            logger.warning(
                "LocalVectorStore.search failed: collection=%s storage_path=%s limit=%s error=%s",
                self.collection_name,
                self.storage_path,
                limit,
                exc,
                exc_info=True,
            )
            return []
        except Exception as exc:
            logger.warning(
                "LocalVectorStore.search failed: collection=%s storage_path=%s limit=%s error=%s",
                self.collection_name,
                self.storage_path,
                limit,
                exc,
                exc_info=True,
            )
            return []

    def upsert(self, points: list[dict[str, Any]], vector_size: int) -> None:
        """Insert or update vector points."""
        if not points:
            return
        client, models = self._get_client_and_models()
        self._ensure_collection(client, models, vector_size)
        try:
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
        except Exception as exc:
            logger.warning(
                "LocalVectorStore.upsert failed: collection=%s storage_path=%s error=%s",
                self.collection_name,
                self.storage_path,
                exc,
                exc_info=True,
            )
            raise

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
                "qdrant-client is required for memory extraction. Install the optional dependency first."
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

    @staticmethod
    def _resolve_storage_path(storage_path: str) -> Path:
        path = Path(storage_path)
        if path.is_absolute():
            return path
        project_root = Path(settings.data_dir).resolve().parent
        return (project_root / path).resolve()

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Local Qdrant wrapper used by hybrid memory (query_points API)."""

from __future__ import annotations

import tempfile
import uuid

from fairyclaw.capabilities.memory_hooks.scripts._vectorstore import LocalVectorStore


def test_memory_local_vector_store_search_finds_upserted_point() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalVectorStore(storage_path=tmp, collection_name="fairyclaw_mem_test")
        dim = 16
        vec = [1.0 / (i + 1) for i in range(dim)]
        pid = str(uuid.uuid4())
        store.upsert(
            points=[
                {
                    "id": pid,
                    "vector": vec,
                    "payload": {"session_id": "sess_test", "gist": "unit gist"},
                }
            ],
            vector_size=dim,
        )
        hits = store.search(
            query_vector=vec,
            limit=5,
            filter_payload={"session_id": "sess_test"},
        )
        assert len(hits) >= 1
        assert hits[0]["payload"].get("gist") == "unit gist"

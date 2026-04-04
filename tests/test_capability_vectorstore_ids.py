# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
from fairyclaw.capabilities.memory_hooks.scripts._vectorstore import LocalVectorStore as MemoryVectorStore
from fairyclaw.capabilities.rag_hooks.scripts._vectorstore import LocalVectorStore as RagVectorStore


def test_vectorstore_normalizes_business_id_to_uuid() -> None:
    point_id = "ragchunk_71787e7ab10044079eb1cd0d88f33043"
    memory_uuid = MemoryVectorStore._normalize_point_id(point_id)
    rag_uuid = RagVectorStore._normalize_point_id(point_id)
    assert memory_uuid == rag_uuid
    assert memory_uuid != point_id
    assert len(memory_uuid) == 36


def test_vectorstore_payload_preserves_original_business_id() -> None:
    point = {
        "id": "ragchunk_demo",
        "payload": {"text": "remember this"},
    }
    memory_payload = MemoryVectorStore._build_payload(point)
    rag_payload = RagVectorStore._build_payload(point)
    assert memory_payload["point_id"] == "ragchunk_demo"
    assert rag_payload["point_id"] == "ragchunk_demo"

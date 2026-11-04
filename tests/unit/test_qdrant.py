"""Qdrant 적재의 재실행·판례 복사·누락 검사를 Fake Client로 확인합니다."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qdrant_client import models

from src.vector_db import ingestion


CHUNK = {
    "document_id": "law:1",
    "chunk_id": "law:1:0001",
    "parent_id": "law:1:article1",
    "title": "시험 법령",
    "document_type": "law",
    "text": "제1조 본문",
    "embedding_text": "시험 법령 | 제1조 본문",
}


class QdrantIngestionTests(unittest.TestCase):
    def test_unchanged_chunk_uses_no_api_tokens_or_upsert(self):
        point_id = ingestion._point_id("chunk", CHUNK["document_id"], CHUNK["chunk_id"])
        stored = SimpleNamespace(id=point_id, payload=ingestion._chunk_payload(CHUNK))
        client = Mock()
        client.retrieve.return_value = [stored]
        openai_client = Mock()
        with (
            patch.object(ingestion, "validate_target_schema"),
            patch.object(ingestion, "load_chunk_rows", return_value=[CHUNK]),
            patch.object(ingestion, "OpenAI", return_value=openai_client),
            patch.object(ingestion, "SparseTextEmbedding"),
        ):
            result = ingestion.ingest_chunks(client, "test-key", expected_count=1)

        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["tokens"], 0)
        openai_client.embeddings.create.assert_not_called()
        client.upsert.assert_not_called()

    def test_precedent_copy_reads_source_and_writes_target_only(self):
        source_payload = {
            "document_type": "precedent",
            "precedent_id": "147076",
            "case_name": "임대차보증금 반환",
            "text": "판례 본문",
        }
        source_point = SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            vector={
                "": [0.1] * ingestion.EMBEDDING_DIMENSION,
                "bm25": models.SparseVector(indices=[7], values=[0.5]),
            },
            payload=source_payload,
        )
        client = Mock()
        client.count.return_value = SimpleNamespace(count=1)
        client.scroll.return_value = ([source_point], None)
        with patch.object(ingestion, "validate_target_schema"):
            copied = ingestion.copy_precedents(client, expected_count=1)

        self.assertEqual(copied, 1)
        self.assertEqual(
            client.count.call_args.kwargs["collection_name"],
            ingestion.SOURCE_COLLECTION,
        )
        self.assertEqual(
            client.count.call_args.kwargs["count_filter"].must[0].match.value,
            "precedent",
        )
        upsert = client.upsert.call_args.kwargs
        self.assertEqual(upsert["collection_name"], ingestion.TARGET_COLLECTION)
        self.assertEqual(upsert["points"][0].payload["source_project"], "3rd")
        self.assertNotIn("source_project", source_payload)
        client.delete.assert_not_called()

    def test_verification_reports_missing_points_without_deleting(self):
        client = Mock()
        source = SimpleNamespace(id="11111111-1111-4111-8111-111111111111")
        with (
            patch.object(ingestion, "load_chunk_rows", return_value=[CHUNK]),
            patch.object(ingestion, "_scroll", side_effect=[iter([source]), iter([])]),
        ):
            with self.assertRaisesRegex(RuntimeError, "missing"):
                ingestion.verify_ingestion(
                    client, expected_chunks=1, expected_precedents=1
                )
        client.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()

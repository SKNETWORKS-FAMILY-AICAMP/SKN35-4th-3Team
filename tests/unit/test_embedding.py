"""4차 청크 임베딩 입력과 벡터 형식을 외부 호출 없이 확인합니다."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.vector_db import ingestion


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "document_id": "guide:1",
            "chunk_id": "guide:1:0001",
            "parent_id": "guide:1:unit",
            "title": "계약 해지 안내",
            "document_type": "official_guide",
            "text": "계약 해지 조건을 확인한다.",
            "embedding_text": "계약 해지 안내 | 계약 해지 조건을 확인한다.",
        }

    def _ingest_with_embedding(self, dense_vector, *, client=None):
        client = client or Mock()
        client.retrieve.return_value = []
        openai_client = Mock()
        openai_client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=dense_vector)],
            usage=SimpleNamespace(total_tokens=12),
        )
        sparse_model = Mock()
        sparse_model.passage_embed.return_value = [
            SimpleNamespace(indices=[3], values=[0.5])
        ]
        with (
            patch.object(ingestion, "validate_target_schema"),
            patch.object(ingestion, "load_chunk_rows", return_value=[self.row]),
            patch.object(ingestion, "OpenAI", return_value=openai_client),
            patch.object(ingestion, "SparseTextEmbedding", return_value=sparse_model),
        ):
            result = ingestion.ingest_chunks(
                client, "test-key", expected_count=1, batch_size=1
            )
        return client, openai_client, sparse_model, result

    def test_saved_chunks_are_valid_embedding_inputs(self):
        rows = ingestion.load_chunk_rows()
        self.assertEqual(len(rows), ingestion.EXPECTED_CHUNKS)
        self.assertTrue(all(row["embedding_text"].strip() for row in rows))

    def test_embedding_text_creates_dense_and_bm25_vectors(self):
        client, openai_client, sparse_model, result = self._ingest_with_embedding(
            [0.1] * ingestion.EMBEDDING_DIMENSION
        )

        self.assertEqual(result, {
            "embedded": 1, "payload_only": 0, "unchanged": 0, "tokens": 12
        })
        self.assertEqual(
            openai_client.embeddings.create.call_args.kwargs["input"],
            [self.row["embedding_text"]],
        )
        self.assertEqual(
            openai_client.embeddings.create.call_args.kwargs["model"],
            "text-embedding-3-small",
        )
        self.assertEqual(
            openai_client.embeddings.create.call_args.kwargs["dimensions"], 1536
        )
        sparse_model.passage_embed.assert_called_once()
        upsert = client.upsert.call_args.kwargs
        self.assertEqual(upsert["collection_name"], ingestion.TARGET_COLLECTION)
        point = upsert["points"][0]
        self.assertEqual(len(point.vector[""]), 1536)
        self.assertEqual(point.vector["bm25"].indices, [3])
        self.assertEqual(point.payload["text"], self.row["text"])

    def test_wrong_dense_dimension_is_not_upserted(self):
        client = Mock()
        with self.assertRaisesRegex(ValueError, "Dense 벡터"):
            self._ingest_with_embedding([0.1] * 1535, client=client)
        client.upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import unittest
import json
from pathlib import Path
import tempfile

from src.evaluation.retrieval_check import (
    EnhancedLexicalIndex, diversify_hits, evaluate, load_cases, normalize_query,
)


class RetrievalCaseTests(unittest.TestCase):
    def test_pending_evidence_is_searched_but_not_counted_as_success_or_failure(self):
        rows = [{"chunk_id": "a", "document_id": "fixture", "text": "벽지 원상회복"}]
        pending = {"query": "벽지 원상회복", "expected_document": None,
                   "evaluation_status": "needs_evidence_review"}
        scored = {"query": "벽지 원상회복", "expected_document": "fixture"}
        result = evaluate(rows, [scored, pending])
        self.assertEqual(result["evaluated_case_count"], 1)
        self.assertEqual(result["unscored_case_count"], 1)
        self.assertEqual(result["hit_at_5"], 1)
        self.assertIsNone(result["cases"][1]["rank_at_5"])
        self.assertTrue(result["cases"][1]["top5"])
        self.assertIsNone(evaluate(rows, [pending])["hit_at_5"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "cases.json"
            path.write_text(json.dumps([pending]), encoding="utf-8")
            self.assertEqual(load_cases(path), [pending])

    def test_normalization_preserves_conditions_and_does_not_select_sources(self):
        query = "알바비를 3개월 동안 못 받았고 사장님이 내일부터 나오지 말래요"
        normalized = normalize_query(query)
        for word in ("임금", "3개월", "못", "사용자", "해고"):
            self.assertIn(word, normalized)
        self.assertNotIn("제26조", normalized)
        self.assertNotIn("001872", normalized)
        self.assertEqual(normalize_query("근로기준법 제36조 금품 청산 14일"), "근로기준법 제36조 금품 청산 14일")

    def test_diversity_removes_same_parent_without_collapsing_all_law_articles(self):
        hits = [{"chunk_id": "a", "parent_id": "p1", "document_id": "law"},
                {"chunk_id": "b", "parent_id": "p1", "document_id": "law"},
                {"chunk_id": "c", "parent_id": "p2", "document_id": "law"},
                {"chunk_id": "d", "parent_id": "p3", "document_id": "law"},
                {"chunk_id": "e", "parent_id": "p4", "document_id": "guide"}]
        self.assertEqual([h["chunk_id"] for h in diversify_hits(hits)], ["a", "c", "e"])
        self.assertEqual(diversify_hits(hits, k=0), [])

    def test_enhanced_search_handles_synonym_on_unlabeled_documents(self):
        rows = [{"chunk_id": "wage", "parent_id": "p1", "document_id": "a",
                 "text": "임금 지급과 금품 청산"},
                {"chunk_id": "rent", "parent_id": "p2", "document_id": "b",
                 "text": "주택 보증금 반환"}]
        index = EnhancedLexicalIndex(rows)
        self.assertEqual(index.search("봉급")[0]["chunk_id"], "wage")
        self.assertEqual(index.search("아무연관없는영문xyz", k=0), [])

    def test_custom_cases_change_ranks_and_expose_exact_excerpt(self):
        rows = [{"chunk_id": "first", "document_id": "fixture", "parent_id": "parent",
                 "locator": "제6조(갱신)", "text": "계약 갱신 조건과 예외", "title": "시험 자료"}]
        cases = [{"query": "계약 갱신", "expected_document": "fixture", "expected_locator": "제6조("},
                 {"query": "계약 갱신", "expected_document": "another"},
                 {"query": "계약 갱신", "expected_document": "fixture", "expected_locator": "제7조("}]
        result = evaluate(rows, cases, preview_chars=5)
        self.assertEqual([c["rank_at_5"] for c in result["cases"]], [1, None, None])
        self.assertAlmostEqual(result["hit_at_5"], 1 / 3)
        hit = result["cases"][0]["top5"][0]
        self.assertEqual(hit["text_preview"], rows[0]["text"][:5])
        self.assertEqual(hit["parent_id"], "parent")
        self.assertTrue(hit["preview_truncated"])

    def test_case_file_validation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "cases.json"
            valid = [{"query": "새 질문", "expected_document": "fixture", "expected_locator": None}]
            path.write_text(json.dumps(valid, ensure_ascii=False), encoding="utf-8-sig")
            self.assertEqual(load_cases(path), valid)
            for invalid in ([], {}, ["query"], [{"query": "", "expected_document": "doc"}],
                            [{"query": "질문"}], [{"query": "질문", "expected_document": "doc", "expected_locator": 6}]):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_cases(path)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path)


if __name__ == "__main__":
    unittest.main()

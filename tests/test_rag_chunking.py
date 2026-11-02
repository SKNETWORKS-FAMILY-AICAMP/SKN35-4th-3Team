import unittest
import hashlib
import json
from pathlib import Path
import tempfile

from src.rag.chunking import make_chunks, split_spans, validate
from src.rag.retrieval_check import (
    EnhancedLexicalIndex, diversify_hits, evaluate, load_cases, normalize_query, parent_evidence,
)
from src.rag.preprocessing import build_units


class ChunkingTests(unittest.TestCase):
    def unit(self, text):
        return {"unit_id": "refund:table", "title": "환불 기준", "locator": "별표 2",
                "text": text, "atomic": True, "embedding_candidate": True}

    def test_large_refund_table_is_recoverable_with_conditions(self):
        text = "조건 | 환급액 | 예외\n" + "이용 전 해지 | 전액 반환 | 별도 약정 확인\n" * 100 + "단, 사업자 귀책이면 배상 기준도 확인한다."
        unit = self.unit(text)
        rows = list(make_chunks(unit, 300, 40))
        validate([unit], rows, 300)
        self.assertGreater(len(rows), 1)
        evidence = parent_evidence(rows[:3], {unit["unit_id"]: unit})
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["text"], text)
        self.assertTrue(all(r["requires_parent_evidence"] for r in rows))

    def test_short_legal_article_is_not_merged_into_another_article(self):
        unit = self.unit("제27조(해고사유 등의 서면통지) 해고사유와 해고시기를 서면으로 통지하여야 한다.")
        rows = list(make_chunks(unit))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], unit["text"])

    def test_no_loss_for_unbroken_text_and_whitespace(self):
        for text in ("가" * 2031, "\n" * 500 + "근거" + " " * 600, "첫 문장.\n" * 300):
            unit = self.unit(text)
            validate([unit], list(make_chunks(unit, 300, 40)), 300)

    def test_invalid_parameters_and_corrupt_offsets_fail(self):
        for size, overlap in ((0, 0), (300, -1), (300, 150), (True, 0)):
            with self.assertRaises(ValueError):
                list(split_spans("본문", size, overlap))
        unit = self.unit("근거 본문")
        rows = list(make_chunks(unit))
        rows[0]["start"] = 1
        with self.assertRaises(ValueError):
            validate([unit], rows, 900)

    def test_source_change_and_review_gates(self):
        # Keep test fixtures inside the project, including on managed Windows hosts.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            raw = root / "data/raw/consumer_sources/example.html"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"original")
            processed = root / "data/processed/consumer_sources/example.json"
            processed.parent.mkdir(parents=True)
            document = {"source_id": "example", "title": "근거", "raw_file": raw.relative_to(root).as_posix(),
                        "raw_sha256": hashlib.sha256(b"original").hexdigest(),
                        "embedding_candidate": True, "units": [
                            {"unit_id": "ok", "role": "rule", "text": "정상 본문"},
                            {"unit_id": "review", "role": "rule", "text": "검토 전 본문", "needs_manual_review": True},
                            {"unit_id": "broken", "role": "rule", "text": "깨진 \ufffd 본문"}]}
            processed.write_text(json.dumps(document), encoding="utf-8")
            units, excluded = build_units(root)
            self.assertEqual([u["unit_id"] for u in units], ["example:ok"])
            self.assertEqual({x["reason"] for x in excluded}, {"manual_review_required", "broken_characters"})
            raw.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                build_units(root)


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

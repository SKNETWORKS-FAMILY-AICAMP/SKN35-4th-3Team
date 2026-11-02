import unittest
import hashlib
import json
import importlib.util
from pathlib import Path
import tempfile

from src.scripts.chunk_data import make_chunks, split_spans, validate
from src.evaluation.retrieval_check import parent_evidence
from src.scripts.data_io import load_processed_data
from src.scripts.preprocess_data import build_evidence_unit
from src.evaluation.table_chunking_experiment import (
    group_pdf_rows, make_condition_chunks, trial_pdf_extraction, validate_pdf_extraction,
)


class ChunkingTests(unittest.TestCase):
    def test_pdf_row_join_keeps_columns_and_marks_incomplete_edges(self):
        pages = [(2, [["이전 페이지의 끝", "", "", "", ""],
                      ["하. 신고 의무", "법 제32", "영업정지", "영업정지", "영업정지"]]),
                 (3, [["위반인 경우", "조제4항", "1개월", "3개월", "6개월"],
                      ["거. 다음 항목", "", "", "", ""]])]
        rows, orphans = group_pdf_rows(pages)
        self.assertEqual(len(orphans), 1)
        self.assertEqual(rows[0]["cells"][2:], ["영업정지 1개월", "영업정지 3개월", "영업정지 6개월"])
        self.assertTrue(rows[0]["end_confirmed"])
        self.assertFalse(rows[1]["end_confirmed"])
        rows, orphans = group_pdf_rows([(2, pages[0][1]), (4, pages[1][1])])
        self.assertFalse(rows[0]["end_confirmed"])
        self.assertEqual(len(orphans), 2)

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "Optional ingestion dependency")
    def test_pdf_sample_matches_reviewed_columns_and_cross_page_row(self):
        result = validate_pdf_extraction()
        if result.get("status") == "sample_not_available":
            self.skipTest("Optional source-data regression check")
        self.assertTrue(result["all_sample_checks_passed"], result.get("manual_sample_checks"))
        self.assertFalse(result["applied_to_production"])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "Optional ingestion dependency")
    def test_pdf_trial_retains_three_sanction_columns(self):
        result = trial_pdf_extraction()
        if "trials" not in result:
            self.skipTest("Optional source-data regression check")
        trial = result["trials"]["vertical_boundaries"]
        self.assertEqual(trial["column_counts"], [5])
        self.assertTrue(any(row[-3:] == ["1개월", "3개월", "6개월"]
                            for table in trial["tables"] for row in table))

    def test_condition_chunks_keep_responsibility_and_time_context(self):
        text = ("<체육시설업>\n┃4) 사업자의 책임있는 사유로 인한 계약해제 │ 기준 │ 비고 ┃\n"
                "┃- 이용개시일 이전 │ 사업자 기준 전 ┃\n┃- 이용개시일 이후 │ 사업자 기준 후 ┃\n"
                "┃5) 소비자의 책임있는 사유로 인한 계약해제 │ 기준 ┃\n"
                "┃- 이용개시일 이전 │ 소비자 기준 전 ┃\n┃- 이용개시일 이후 │ 소비자 기준 후 ┃\n"
                "【참고】 관련 법령\n예외는 전체 표에서 확인한다.")
        unit = {**self.unit(text), "content_type": "industry_table", "calculation_restricted": True}
        rows = list(make_condition_chunks(unit))
        validate([unit], rows, 900)
        for row in rows:
            self.assertTrue(row["calculation_restricted"])
            self.assertTrue(row["requires_parent_evidence"])
            for span in row["condition_label_sources"]:
                self.assertIn(text[span["start"]:span["end"]].strip(), row["condition_label"])
        consumer = next(row for row in rows if "소비자 기준 전" in row["text"])
        self.assertIn("소비자의 책임", consumer["condition_label"])
        self.assertIn("이용개시일 이전", consumer["condition_label"])
        self.assertNotIn("사업자의 책임", consumer["condition_label"])
        reference = next(row for row in rows if "【참고】" in row["text"])
        self.assertNotIn("소비자의 책임", reference["condition_label"])

    def test_condition_experiment_leaves_other_units_unchanged(self):
        unit = self.unit("짧지만 완전한 조문이다.")
        self.assertEqual(list(make_chunks(unit)), list(make_condition_chunks(unit)))

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
            common = {"document_id": "example", "title": "근거", "source_path": raw.relative_to(root).as_posix(),
                      "raw_sha256": hashlib.sha256(b"original").hexdigest()}
            units = []
            for key, text, reason in [("ok", "정상 본문", None),
                                      ("review", "검토 전 본문", "manual_review_required"),
                                      ("broken", "깨진 \ufffd 본문", None)]:
                unit, exclusion = build_evidence_unit(common, key, text, key, reason=reason)
                units.append(unit)
                if key != "ok":
                    self.assertIsNotNone(exclusion)
            document = {"schema_version": 3, "units": units}
            processed.write_text(json.dumps(document), encoding="utf-8")
            loaded = load_processed_data(root / "data/processed", raw_dir=root / "data/raw")
            self.assertEqual([u["unit_id"] for u in loaded], ["example:ok"])
            raw.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_processed_data(root / "data/processed", raw_dir=root / "data/raw")


if __name__ == "__main__":
    unittest.main()

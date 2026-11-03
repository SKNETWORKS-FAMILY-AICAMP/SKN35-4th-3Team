import unittest
import hashlib
import json
import importlib.util
from pathlib import Path
import tempfile

from src.scripts.chunk_data import make_chunks, split_spans, split_legal_spans, validate
from src.scripts.data_io import load_processed_data
from src.scripts.preprocess_data import build_evidence_unit
from src.scripts.pdf_tables import group_pdf_rows, validate_pdf_extraction


class ChunkingTests(unittest.TestCase):
    def test_legal_split_keeps_each_fitting_paragraph_and_proviso_whole(self):
        paragraphs = ["① 본문 " + "가" * 180 + ". 다만, 예외 조건을 확인한다.\n",
                      "② 다음 기준 " + "나" * 180 + ".\n",
                      "③ 마지막 기준 " + "다" * 180 + "."]
        text = "제1조(기준)\n" + "".join(paragraphs)
        spans = list(split_legal_spans(text, 300, 40))
        for paragraph in paragraphs:
            start = text.index(paragraph)
            self.assertTrue(any(a <= start and b >= start + len(paragraph) for a, b in spans))
        self.assertTrue(all(b - a <= 300 for a, b in spans))

    def test_split_long_list_repeats_only_its_own_paragraph_intro(self):
        intro = "① 다음 각 호의 경우에는 제한한다. 다만, 사전 동의가 있으면 제외한다.\n"
        text = ("제17조(청약철회등)\n" + intro
                + "1. 첫 번째 사유 " + "가" * 180 + "\n"
                + "2. 두 번째 사유 " + "나" * 180 + "\n"
                + "3. 세 번째 사유 " + "다" * 180 + "\n② 별도의 기준이다.")
        unit = {**self.unit(text), "unit_id": "009318_282793_20260721:article19",
                "content_type": "main_article"}
        rows = list(make_chunks(unit, 300, 40))
        validate([unit], rows, 300)
        continuation = next(row for row in rows if row["text"].startswith("2."))
        self.assertIn(intro.strip(), continuation["retrieval_context"])
        self.assertNotIn("② 별도의 기준", continuation["retrieval_context"])
        for source in continuation["retrieval_context_sources"]:
            self.assertEqual(source["text"], text[source["start"]:source["end"]])
        continuation["retrieval_context_sources"][0]["start"] += 1
        with self.assertRaisesRegex(ValueError, "Retrieval context"):
            validate([unit], rows, 300)

    def test_oversized_proviso_keeps_parent_link_without_inventing_context(self):
        text = "제17조(청약철회등)\n① 기본 조건 " + "가" * 400 + ". 다만, " + "나" * 450 + "."
        unit = {**self.unit(text), "unit_id": "009318_282793_20260721:article19",
                "content_type": "main_article"}
        rows = list(make_chunks(unit, 300, 40))
        validate([unit], rows, 300)
        for row in rows[1:]:
            self.assertIn("부모 근거 전체 확인", row["retrieval_context"])
            self.assertTrue(row["requires_parent_evidence"])
            self.assertIn("①", row["retrieval_context"])

    def test_reviewed_articles_keep_all_paragraphs_and_other_articles_stay_unchanged(self):
        from src.scripts.chunk_data import PARAGRAPH_PATTERN, REVIEWED_LONG_ARTICLES
        from src.scripts.data_io import ROOT

        if not (ROOT / "data/processed/laws").exists():
            self.skipTest("Optional source-data regression check")
        units = [u for u in load_processed_data() if u["unit_id"] in REVIEWED_LONG_ARTICLES]
        self.assertEqual(len(units), 3)
        for unit in units:
            rows = list(make_chunks(unit))
            validate([unit], rows, 900)
            starts = [m.start() for m in PARAGRAPH_PATTERN.finditer(unit["text"])]
            for start, end in zip(starts, starts[1:] + [len(unit["text"])]):
                self.assertTrue(any(r["start"] <= start and r["end"] >= end for r in rows), unit["locator"])
            other = {**unit, "unit_id": "unreviewed:article"}
            self.assertEqual([(r["start"], r["end"]) for r in make_chunks(other)],
                             list(split_spans(unit["text"])))
            self.assertTrue(all("retrieval_context" not in r for r in make_chunks(other)))

    def test_refund_context_keeps_mixed_scopes_and_source_offsets(self):
        from src.scripts.data_io import ROOT

        path = ROOT / "data/processed/consumer_sources/consumer_dispute_standard.json"
        if not path.exists():
            self.skipTest("Optional source-data regression check")
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        unit = next(u for u in document["units"]
                    if u["unit_id"] == "consumer_dispute_standard:industry:fitness_refund")
        rows = list(make_chunks(unit))
        validate([unit], rows, 900)
        self.assertEqual([(r["start"], r["end"]) for r in rows], list(split_spans(unit["text"])))
        mixed = next(r for r in rows if "4)" in r["retrieval_context"] and "5)" in r["retrieval_context"])
        self.assertIn("사업자의 책임", mixed["retrieval_context"])
        self.assertIn("소비자의 책임", mixed["retrieval_context"])
        continuation = next(r for r in rows if r["start"] == 1532)
        self.assertIn("소비자의 책임", continuation["retrieval_context"])
        self.assertIn("이용개시일 이후", continuation["retrieval_context"])
        self.assertNotIn("사업자의 책임", continuation["retrieval_context"])
        for row in rows:
            self.assertEqual(row["text"], unit["text"][row["start"]:row["end"]])
            for source in row["retrieval_context_sources"]:
                self.assertEqual(source["text"], unit["text"][source["start"]:source["end"]])
            self.assertIn("표의 열:", row["embedding_text"])
            self.assertTrue(row["requires_parent_evidence"])
            self.assertTrue(row["calculation_restricted"])
            self.assertEqual(row["supplemental_sources"], unit["supplemental_sources"])
        rows[0]["retrieval_context_sources"][0]["start"] += 1
        with self.assertRaisesRegex(ValueError, "Retrieval context"):
            validate([unit], rows, 900)
        # 작은 청크에서도 이전 구간의 조건이 잘못 이어지지 않아야 한다.
        validate([unit], list(make_chunks(unit, 300, 40)), 300)

    def test_other_units_do_not_receive_refund_context(self):
        unit = self.unit("소비자의 책임으로 해지하는 경우의 일반 안내")
        row = next(make_chunks(unit))
        self.assertNotIn("retrieval_context", row)
        self.assertEqual(row["embedding_text"], f"[{unit['title']} / {unit['locator']}]\n{unit['text']}")

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

    def unit(self, text):
        return {"unit_id": "refund:table", "title": "환불 기준", "locator": "별표 2",
                "text": text, "atomic": True, "embedding_candidate": True}

    def test_large_refund_table_is_recoverable_with_conditions(self):
        text = "조건 | 환급액 | 예외\n" + "이용 전 해지 | 전액 반환 | 별도 약정 확인\n" * 100 + "단, 사업자 귀책이면 배상 기준도 확인한다."
        unit = self.unit(text)
        rows = list(make_chunks(unit, 300, 40))
        validate([unit], rows, 300)
        self.assertGreater(len(rows), 1)
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

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from src.scripts.preprocess_data import ROOT, html_extract, hwpx_extract, preprocess_data, xml_extract
from src.scripts.chunk_data import chunk_data
from src.scripts.data_io import load_processed_data
from src.scripts.preprocess_data import build_pdf_entries, get_exclusion_reason, preprocess_file


class RawPreprocessingTests(unittest.TestCase):
    def test_pdf_entries_keep_heading_tables_and_review_precedence(self):
        def page(number, body, images=None):
            return {"page_number": number, "body_text": body,
                    "images": images or [], "removed_margin_lines": [],
                    "tables": [{"rows": [["조건", "금액"], [None, "전액"]], "bbox": [0, 0, 1, 1]}]}
        pages = [page(1, "1. 안내 제목"),
                 page(2, "계속되는 내용", [{"width": 100, "height": 100}])]
        entries = list(build_pdf_entries(Path("guides/manual.pdf"), pages))
        self.assertEqual(entries[0]["reason"], "cover_or_toc")
        self.assertEqual(entries[2]["reason"], "image_layout_review_required")
        self.assertIn("1. 안내 제목", entries[2]["locator"])
        self.assertEqual(entries[3]["reason"], entries[2]["reason"])
        self.assertEqual(entries[3]["table"]["rows"][1][0], None)
        form_entries = list(build_pdf_entries(Path("law_appendices/example_000100F.pdf"), pages))
        self.assertTrue(all(e["reason"] == "application_form_reference_only" for e in form_entries))
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            raw_dir = Path(folder)
            path = raw_dir / "guides/manual.pdf"
            path.parent.mkdir()
            path.write_bytes(b"mocked PDF")
            with patch("src.scripts.preprocess_data.extract_pdf", return_value=pages):
                result = preprocess_file(path, raw_dir, {})
            self.assertEqual(result["record"]["extracted_units"], 4)
            self.assertEqual(result["record"]["indexed_units"], 0)
            self.assertEqual(len(result["exclusions"]), 4)

    def test_failed_extraction_keeps_existing_processed_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder).resolve()
            raw, processed = root / "incoming", root / "cleaned"
            raw.mkdir()
            (raw / "invalid.xml").write_text("<broken>", encoding="utf-8")
            processed.mkdir()
            sentinel = processed / "existing.json"
            sentinel.write_bytes(b"previous output")
            with self.assertRaises(ValueError):
                preprocess_data(raw, processed, root=root, report_dir=root / "reports")
            self.assertEqual(sentinel.read_bytes(), b"previous output")
            self.assertEqual(list(processed.iterdir()), [sentinel])
            report = json.loads((root / "reports/quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["file_status_counts"], {"failed": 1})
        self.assertEqual(get_exclusion_reason("", reason="explicit", historical=True), "explicit")
        self.assertEqual(get_exclusion_reason("\ufffd", historical=True), "historical_reference_not_current")
        self.assertEqual(get_exclusion_reason("\ufffd"), "broken_characters")

    def test_independent_stages_with_custom_paths(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder).resolve()
            raw_dir, processed_dir, chunk_dir = root / "incoming", root / "cleaned", root / "pieces"
            raw_file = raw_dir / "consumer_sources/sample.xml"
            raw_file.parent.mkdir(parents=True)
            raw_file.write_text('<AdmRulService><행정규칙기본정보><행정규칙명>시험 고시</행정규칙명></행정규칙기본정보><조문내용>계약해지 조건과 예외를 확인한다.</조문내용></AdmRulService>', encoding="utf-8")
            messages = []
            result = preprocess_data(str(raw_dir), str(processed_dir), root=root,
                                     report_dir=root / "reports", progress=messages.append)
            self.assertEqual(result["processed_file_count"], 1)
            self.assertFalse(chunk_dir.exists())
            self.assertFalse((root / "data/chunk").exists())
            self.assertEqual(len(messages), 1)
            before = {p: p.read_bytes() for p in processed_dir.rglob("*.json")}
            chunks = chunk_data(str(processed_dir), str(chunk_dir), raw_dir=str(raw_dir),
                                root=root, chunk_size=300, overlap=40, report_dir=root / "reports")
            self.assertEqual(chunks["chunk_count"], 1)
            self.assertEqual(len(load_processed_data(processed_dir, raw_dir=raw_dir)), 1)
            self.assertEqual(before, {p: p.read_bytes() for p in processed_dir.rglob("*.json")})
            self.assertTrue((chunk_dir / "consumer_sources/chunks.jsonl").exists())
            original = (chunk_dir / "consumer_sources/chunks.jsonl").read_bytes()
            raw_file.write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                chunk_data(processed_dir, chunk_dir, raw_dir=raw_dir, root=root)
            self.assertEqual(original, (chunk_dir / "consumer_sources/chunks.jsonl").read_bytes())

    def test_missing_or_overlapping_inputs_fail_without_outputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder).resolve()
            with self.assertRaises(FileNotFoundError):
                preprocess_data(root / "missing", root / "processed", root=root)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            with self.assertRaises(ValueError):
                preprocess_data(raw_dir, raw_dir / "processed", root=root)
            with self.assertRaises(ValueError):
                chunk_data(root / "processed", root / "chunks", chunk_size=0, root=root)
            self.assertFalse((root / "chunks").exists())

    def test_nested_article_and_branch_are_not_lost_or_duplicated(self):
        xml = '''<법령><기본정보><법령명_한글>시험법</법령명_한글></기본정보><조문>
        <조문단위><조문여부>조문</조문여부><조문번호>6</조문번호><조문가지번호>2</조문가지번호>
        <조문제목>계약 해지</조문제목><조문내용>본문 조건</조문내용>
        <항><항내용>예외 조건</항내용><호><호내용>세부 요건</호내용></호></항></조문단위>
        </조문></법령>'''
        title, entries = xml_extract(xml)
        self.assertEqual(title, "시험법")
        self.assertEqual(entries[0]["locator"], "제6조의2(계약 해지)")
        self.assertEqual(entries[0]["text"], "본문 조건\n예외 조건\n세부 요건")

    def test_administrative_appendix_is_extracted_from_xml(self):
        xml = '''<AdmRulService><행정규칙기본정보><행정규칙명>환급 고시</행정규칙명></행정규칙기본정보>
        <조문내용>계약 해지 조건</조문내용><별표><별표단위><별표번호>1</별표번호>
        <별표제목>환급 기준</별표제목><별표내용>이용 전 | 전액 환급 | 예외 있음</별표내용>
        </별표단위></별표></AdmRulService>'''
        _, entries = xml_extract(xml)
        self.assertEqual(len(entries), 2)
        self.assertIn("전액 환급", entries[1]["text"])
        self.assertTrue(entries[1]["calculation_restricted"])

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "Optional ingestion dependency")
    def test_html_utf8_tables_and_script_removal(self):
        source = '<div class="board_view"><h1>환불 조건</h1><script>secret_menu()</script><table><tr><th>조건</th><th>금액</th></tr><tr><td rowspan="2">해지</td><td>10,000원</td></tr></table></div>'
        text, meta = html_extract(source.encode("utf-8"), "kca_case_fixture.html")
        self.assertIn("환불 조건", text)
        self.assertNotIn("secret_menu", text)
        self.assertEqual(meta["tables"][0]["rows"][1][0]["rowspan"], "2")
        self.assertEqual(meta["encoding"], "utf-8-sig")

    def test_hwpx_text_runs_join_and_table_spans_remain(self):
        xml = '<section><p><run><t>계약</t></run><run><t>해지</t></run></p><tbl><tr><tc><cellSpan rowSpan="2" colSpan="1"/><p><t>전액 환급</t></p></tc></tr></tbl></section>'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "terms.hwpx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Contents/section0.xml", xml)
            text, meta = hwpx_extract(path)
        self.assertIn("계약해지", text)
        self.assertEqual(meta["tables"][0]["rows"][0][0]["spans"][0]["rowSpan"], "2")

    def test_preprocessing_ignores_old_body(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            raw = root / "data/raw/consumer_sources/source.xml"
            raw.parent.mkdir(parents=True)
            raw.write_text('<AdmRulService><행정규칙기본정보><행정규칙명>원본</행정규칙명></행정규칙기본정보><조문내용>원본 계약 조건</조문내용></AdmRulService>', encoding="utf-8")
            processed = root / "data/processed/consumer_sources/source.json"
            processed.parent.mkdir(parents=True)
            processed.write_text(json.dumps({"source_id": "source", "title": "기존 제목", "raw_file": "data/raw/consumer_sources/source.xml", "units": [{"text": "잘못된 이전 본문"}]}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                report = preprocess_data(root=root)
                chunk_data(root=root)
            output = (root / "data/processed/consumer_sources/source.json").read_text(encoding="utf-8")
            self.assertIn("원본 계약 조건", output)
            self.assertNotIn("잘못된 이전 본문", output)
            self.assertFalse(report["old_processed_body_text_used"])
            self.assertTrue((root / "data/chunk/consumer_sources/chunks.jsonl").is_file())
            self.assertFalse((root / "data/processed/parents.jsonl").exists())
            self.assertFalse((root / "data/source_catalog.json").exists())

    def test_real_refund_sections_keep_scope_and_offsets(self):
        files = list((ROOT / "data/raw/consumer_sources").glob("consumer_dispute_standard_*.xml"))
        if not files:
            self.skipTest("Optional source-data regression check")
        _, entries = xml_extract(files[0].read_bytes())
        targets = [e for e in entries if e["content_type"] in {"industry_table", "product_table"}]
        self.assertEqual(len(targets), 4)
        self.assertEqual({d for e in targets for d in e["domains"]}, {"fitness_refund", "shopping_return"})
        self.assertTrue(all(e["source_end"] > e["source_start"] for e in targets))


if __name__ == "__main__":
    unittest.main()

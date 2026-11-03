import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from src.scripts.preprocess_data import ROOT, html_extract, hwpx_extract, preprocess_data, xml_extract
from src.scripts.chunk_data import chunk_data
from src.scripts.data_io import load_processed_data
from src.scripts.preprocess_data import build_evidence_unit, build_pdf_entries, get_exclusion_reason, preprocess_file


class RawPreprocessingTests(unittest.TestCase):
    def test_reviewed_appendix_formats_share_origin_but_keep_distinct_parents(self):
        from src.scripts.preprocess_data import attach_canonical_source, REVIEWED_APPENDIX_MEMBERS
        from src.scripts.chunk_data import make_chunks

        linked = []
        for identity, digest in REVIEWED_APPENDIX_MEMBERS.items():
            unit = {"unit_id": identity, "raw_sha256": digest, "title": "근로기준법 시행령",
                    "locator": "별표 2", "text": "근로계약서를 작성하여 근로자에게 내주어야 한다."}
            result = attach_canonical_source(unit)
            self.assertNotIn("canonical_source_id", unit)
            self.assertEqual(result["text"], unit["text"])
            chunk = next(make_chunks(result))
            self.assertEqual(chunk["canonical_source_id"], result["canonical_source_id"])
            self.assertEqual(chunk["parent_id"], identity)
            linked.append(result)
        self.assertEqual(linked[0]["canonical_source_id"], linked[1]["canonical_source_id"])
        self.assertNotEqual(linked[0]["unit_id"], linked[1]["unit_id"])
        self.assertEqual(linked[0]["canonical_source_scope"], "same_version_appendix_document")

    def test_canonical_source_rejects_changed_hash_and_does_not_group_other_versions(self):
        from src.scripts.preprocess_data import attach_canonical_source, REVIEWED_APPENDIX_MEMBERS

        identity = next(iter(REVIEWED_APPENDIX_MEMBERS))
        with self.assertRaisesRegex(ValueError, "canonical source changed"):
            attach_canonical_source({"unit_id": identity, "raw_sha256": "modified"})
        for other in ("003058_another_version:appendix1", "003058_270551_20251023:appendix0"):
            unit = {"unit_id": other, "raw_sha256": "unreviewed", "text": "같은 제목"}
            self.assertIs(attach_canonical_source(unit), unit)

    def test_pdf_heading_uses_toc_titles_and_ignores_numbered_body_items(self):
        from src.scripts.preprocess_data import pdf_heading_contexts

        pages = [
            {"page_number": 1, "body_text": "1. 큰 제목 . . . . . 2\n1.1. 첫 절 . . . . . 2\n1.2. 다음 절 . . . . . 4"},
            {"page_number": 2, "body_text": "1. 큰 제목\n1.1. 첫 절\n정상 본문"},
            {"page_number": 3, "body_text": "10. 25. 선고 99도4837 판결\n1. 재직증명서\n2. 임금명세서"},
            {"page_number": 4, "body_text": "1.2. 다음 절\n새 본문"},
        ]
        headings = pdf_heading_contexts(pages)
        self.assertEqual(headings[1], "")
        self.assertEqual(headings[2], "1. 큰 제목 > 1.1. 첫 절")
        self.assertEqual(headings[3], headings[2])
        self.assertEqual(headings[4], "1.2. 다음 절")
        self.assertEqual(pdf_heading_contexts([{"page_number": 1, "body_text": "1. 임의 목록"}]), {})

    def test_pdf_heading_keeps_sibling_sections_separate(self):
        from src.scripts.preprocess_data import pdf_heading_contexts

        pages = [
            {"page_number": 1, "body_text": "1. 안내 . . . . . 2\n1.1. 계약 . . . . . 2\n1.2. 해지 . . . . . 2"},
            {"page_number": 2, "body_text": "1. 안내\n1.1. 계약\n계약 본문\n1.2. 해지\n해지 본문"},
            {"page_number": 3, "body_text": "해지 본문 계속"},
        ]
        headings = pdf_heading_contexts(pages)
        self.assertEqual(headings[2], "1. 안내 > 1.1. 계약 | 1. 안내 > 1.2. 해지")
        self.assertEqual(headings[3], "1. 안내 > 1.2. 해지")

    def test_real_guide_heading_removes_citation_noise_without_changing_evidence(self):
        from src.scripts.preprocess_data import clean

        path = ROOT / "data/processed/guides/guide_cad7b1de1e4dc64c.json"
        if not path.exists():
            self.skipTest("Optional source-data regression check")
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = {e["key"]: e for e in build_pdf_entries(Path(document["source_path"]), document["pages"])}
        self.assertIn("해고의 제한 및 적용범위", entries["p13:body"]["locator"])
        self.assertNotIn("선고", entries["p28:body"]["locator"])
        self.assertIn("미지급 임금", entries["p78:body"]["locator"])
        self.assertNotIn("재직증명서", entries["p95:body"]["locator"])
        for unit in document["units"]:
            key = unit["unit_id"].split(":", 1)[1]
            self.assertEqual(unit["text"], clean(entries[key]["text"]))
            if "table" in unit:
                self.assertEqual(unit["table"], entries[key]["table"])

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "Optional ingestion dependency")
    def test_board_screen_elements_are_archived_and_body_tables_remain(self):
        source = '''<div id="contentBody"><table class="board_insert">
        <caption>피해 안내 게시글 상세보기 - 등록일, 조회수</caption>
        <tr><th class="title">피해 안내</th></tr>
        <tr><th>등록일</th><td>2024-12-11</td><th>조회수</th><td>5027</td></tr>
        <tr><th>첨부파일</th><td><a href="guide.pdf">안내.pdf</a></td></tr>
        <tr><td class="substance"><p>환불 신청 등록일과 삭제 안내도 증빙으로 보관하세요.</p>
        <table><tr><th>조건</th><th>환급</th></tr><tr><td>해지</td><td>잔여대금</td></tr></table></td></tr>
        <tr><th>이전글</th><td class="prev_cont">다른 보험 안내</td></tr></table>
        <div class="footerIn"><div class="con_manager">게시물담당자</div>
        <div id="popup_layer" class="popup_layer">삭제사유 제17조(홈페이지 게시물 관리)</div></div></div>'''
        text, meta = html_extract(source.encode(), "kca_fitness_closure_fixture.html")
        self.assertIn("환불 신청 등록일과 삭제 안내도 증빙으로 보관하세요.", text)
        self.assertIn("잔여대금", text)
        self.assertIn("안내.pdf", text)
        for noise in ("5027", "다른 보험 안내", "홈페이지 게시물 관리", "게시글 상세보기"):
            self.assertNotIn(noise, text)
            self.assertTrue(any(noise in item["text"] for item in meta["excluded_screen_elements"]))
        self.assertEqual(meta["page_metadata"], {"등록일": ["2024-12-11"], "조회수": ["5027"]})
        self.assertTrue(any(table["rows"][0][0]["text"] == "조건" for table in meta["tables"]))
        other_text, other_meta = html_extract(source.encode(), "other_site.html")
        self.assertIn("다른 보험 안내", other_text)
        self.assertNotIn("excluded_screen_elements", other_meta)

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "Optional ingestion dependency")
    def test_real_gym_board_keeps_full_article_and_separates_screen_elements(self):
        from lxml import html
        from src.scripts.preprocess_data import decode_html

        path = ROOT / "data/raw/consumer_sources/kca_fitness_closure_gym_c624ab16f714fd3f.html"
        if not path.exists():
            self.skipTest("Optional source-data regression check")
        data = path.read_bytes()
        text, meta = html_extract(data, path.name)
        tree = html.fromstring(decode_html(data)[0])
        article = tree.xpath('//td[@class="substance"]')[0]
        article_text = re.sub(r"\s+", "", article.text_content())
        self.assertIn(article_text, re.sub(r"\s+", "", text))
        self.assertIn("등록일", meta["page_metadata"])
        self.assertNotIn("홈페이지 게시물 관리", text)
        self.assertNotIn("실손보험, 치료·입원 불인정 다툼 많아", text)
        self.assertEqual(len(meta["excluded_screen_elements"]), 5)

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "Optional ingestion dependency")
    def test_submission_popup_is_separated_without_removing_general_evidence(self):
        source = '''<div id="content"><h1>피해구제 접수방법</h1>
        <p>증빙서류: 계약서, 영수증, 사업자에게 이의를 제기한 전자문서</p>
        <div id="layerPopupNotice1" class="layer_pop pgnotice"><h2>기간 한정 공지</h2><p>특정 업체 사건</p></div>
        <div id="generalNotice" class="notice"><p>일반적인 신청 유의사항</p></div></div>'''
        text, meta = html_extract(source.encode(), "kca_redress_submission_fixture.html")
        self.assertIn("계약서, 영수증", text)
        self.assertIn("일반적인 신청 유의사항", text)
        self.assertNotIn("기간 한정 공지", text)
        self.assertNotIn("특정 업체 사건", text)
        self.assertIn("특정 업체 사건", meta["excluded_popups"][0]["text"])
        self.assertEqual(meta["excluded_popups"][0]["id"], "layerPopupNotice1")
        text, meta = html_extract(source.encode(), "other_site.html")
        self.assertIn("특정 업체 사건", text)
        self.assertNotIn("excluded_popups", meta)

    def test_pdf_example_context_stops_before_normal_guidance(self):
        from src.scripts.preprocess_data import pdf_example_sections

        first = "※ 청약철회 방해문구 사례\n구매자에게 불리한 판매자 문구의 예시입니다.\n<예시>\n반품 불가"
        second = "포장을 개봉하면 반품 불가\n< 출처: 2021년 전자거래분쟁조정사례집, 56~57쪽 >\n물품반환 비용 부담\n사업자 귀책 시 사업자 부담"
        pages = [{"page_number": 29, "body_text": first}, {"page_number": 30, "body_text": second}]
        sections = pdf_example_sections(pages)
        self.assertEqual("".join(s["original_text"] for s in sections[30]), second)
        self.assertIn("청약철회 방해문구 사례", sections[30][0]["text"])
        self.assertEqual(sections[30][0]["context_source"]["page_number"], 29)
        self.assertNotIn("사업자 귀책", sections[30][0]["text"])
        self.assertEqual(sections[30][1]["role"], "guidance")
        self.assertNotIn("반품 불가", sections[30][1]["text"])
        pages[1]["body_text"] = "다음 페이지의 다른 내용"
        with self.assertRaises(ValueError):
            pdf_example_sections(pages)

    def test_real_pdf_example_context_reaches_every_retrieval_chunk(self):
        from src.scripts.data_io import read_json
        from src.scripts.chunk_data import make_chunks

        path = ROOT / "data/processed/guides/guide_383b62bce56bf1ce.json"
        if not path.exists():
            self.skipTest("Optional source-data regression check")
        entries = list(build_pdf_entries(Path("guides/[pdf]인터넷 쇼핑.pdf"), read_json(path)["pages"]))
        quote = next(e for e in entries if e["key"] == "p30:body")
        rules = next(e for e in entries if e["key"] == "p30:body:rules")
        self.assertIn("판매자의 위법 부당 행위", quote["text"])
        self.assertNotIn("물품반환 비용 부담", quote["text"])
        self.assertIn("제18조제10항", rules["text"])
        unit = {**quote, "unit_id": "shopping:quote", "title": "인터넷 쇼핑"}
        self.assertTrue(all("청약철회 방해문구 사례" in row["embedding_text"] for row in make_chunks(unit, 300, 40)))

    def test_supplemental_pdf_hash_and_path_are_checked(self):
        import hashlib
        from src.scripts.data_io import validate_source_hashes

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder).resolve()
            raw = root / "data/raw/laws/source.xml"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"raw source")
            pdf = root / "docs/data_reports/source_verification/example.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"reviewed PDF")
            unit = {"source_path": "data/raw/laws/source.xml", "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                    "supplemental_sources": [{"path": pdf.relative_to(root).as_posix(),
                                              "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}]}
            with patch("src.scripts.data_io.ROOT", root):
                validate_source_hashes([unit], root / "data/raw")
                pdf.write_bytes(b"modified PDF")
                with self.assertRaisesRegex(ValueError, "Supplemental source changed"):
                    validate_source_hashes([unit], root / "data/raw")
                unit["supplemental_sources"][0]["path"] = "docs/data_reports/source_verification/../../outside.pdf"
                with self.assertRaisesRegex(ValueError, "escapes"):
                    validate_source_hashes([unit], root / "data/raw")

    def test_fraction_geometry_uses_position_instead_of_extraction_order(self):
        from src.scripts.pdf_tables import fraction_pairs

        def box(top, x=300):
            return {"x0": x, "x1": x + 60, "top": top, "bottom": top + 8}
        numerators = [box(200), box(100)]
        denominators = [box(112), box(212)]
        pairs = fraction_pairs(numerators, denominators)
        self.assertEqual([(a["top"], b["top"]) for a, b in pairs], [(100, 112), (200, 212)])
        for invalid in ([box(90)], [box(112, x=400)], []):
            with self.assertRaises(ValueError):
                fraction_pairs([box(100)], invalid)
        with self.assertRaises(ValueError):
            fraction_pairs([box(100)], [box(112), box(212)])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "Optional ingestion dependency")
    def test_refund_attachment_confirms_four_fractions_with_scope_notes(self):
        from src.scripts.pdf_tables import REFUND_PDF, verify_refund_formulas

        if not REFUND_PDF.exists():
            self.skipTest("Optional verification attachment")
        result = verify_refund_formulas(load_processed_data())
        self.assertEqual(result["missing_fractions_in_xml"], 4)
        self.assertEqual(len(result["verified_formulas"]), 4)
        self.assertTrue(all(result["context_checks"].values()))
        self.assertEqual({(f["responsibility"], f["contract_basis"]) for f in result["verified_formulas"]},
                         {("business", "period"), ("business", "count"), ("consumer", "period"), ("consumer", "count")})
        self.assertTrue(all(not f["calculation_enabled"] for f in result["verified_formulas"]))
        self.assertTrue(result["raw_xml_matches_preserved_original"])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "Optional ingestion dependency")
    def test_refund_supplement_is_traceable_complete_and_idempotent(self):
        from src.scripts.pdf_tables import REFUND_PDF, apply_refund_formulas, verify_refund_formulas
        from src.scripts.chunk_data import make_chunks, validate
        from src.scripts.preprocess_data import catalog

        path = ROOT / "data/raw/consumer_sources/consumer_dispute_standard_64e922b28e0d2e2b.xml"
        if not REFUND_PDF.exists() or not path.exists():
            self.skipTest("Optional verification sources")
        result = preprocess_file(path, ROOT / "data/raw", catalog(ROOT))
        self.assertEqual(result["record"]["status"], "extracted")
        unit = next(u for u in result["units"] if u["unit_id"].endswith(":industry:fitness_refund"))
        self.assertTrue(unit["calculation_restricted"])
        supplement = unit["evidence_metadata"]["formula_supplement"]
        original = unit["text"]
        for correction in reversed(supplement["corrections"]):
            start = correction["processed_start"]
            original = original[:start] + correction["before"] + original[start + len(correction["after"]):]
        self.assertEqual(original, supplement["original_text"])
        self.assertEqual(len(supplement["corrections"]), 4)
        self.assertEqual(apply_refund_formulas(unit), unit)
        self.assertTrue(all(f["applied_to_processed"] for f in verify_refund_formulas([unit])["verified_formulas"]))
        chunks = list(make_chunks(unit))
        validate([unit], chunks, 900)
        self.assertTrue(all(c["supplemental_sources"] == unit["supplemental_sources"] for c in chunks))

    def test_reviewed_pdf_requires_the_reviewed_source_hash(self):
        from src.scripts.pdf_tables import REVIEWED_PDF_NAME, reviewed_pdf_entries

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / REVIEWED_PDF_NAME
            path.write_bytes(b"changed PDF")
            with self.assertRaisesRegex(ValueError, "Reviewed PDF changed"):
                reviewed_pdf_entries(path, [])
            self.assertEqual(reviewed_pdf_entries(Path(folder) / "other.pdf", []), [])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "Optional ingestion dependency")
    def test_reviewed_pdf_rows_include_common_conditions_and_source_fragments(self):
        from src.scripts.pdf_tables import REVIEWED_PDF_NAME, reviewed_pdf_entries
        from src.scripts.preprocess_data import extract_pdf

        path = ROOT / "data/raw/law_appendices" / REVIEWED_PDF_NAME
        if not path.exists():
            self.skipTest("Optional source-data regression check")
        entries = reviewed_pdf_entries(path, extract_pdf(path))
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertIn("1. 일반기준", entry["text"])
            self.assertIn("2. 개별기준", entry["text"])
            self.assertIn(1, entry["page_numbers"])
            self.assertTrue(entry["evidence_metadata"]["fragments"])
            self.assertTrue(entry["calculation_restricted"])

    def test_non_content_is_excluded_but_short_articles_remain(self):
        common = {"document_id": "sample", "source_path": "data/raw/laws/sample.json"}
        for text in ("[별표 1] 삭제 <2018. 12. 31.>", "[별지 제3호서식] 삭제 <1994.11.14>"):
            unit, exclusion = build_evidence_unit(common, "deleted", text, "별표", content_type="appendix")
            self.assertFalse(unit["embedding_candidate"])
            self.assertEqual(unit["text"], text)
            self.assertEqual(exclusion["reason"], "deleted_notice")
        unit, exclusion = build_evidence_unit(common, "heading", "제2조의2", "제2조의2", content_type="main_article")
        self.assertFalse(unit["embedding_candidate"])
        self.assertEqual(exclusion["reason"], "heading_only")
        for text in ("제68조(임금의 청구) 미성년자는 독자적으로 임금을 청구할 수 있다.",
                     "제1조(삭제 요청) 개인정보 삭제를 요청할 수 있다."):
            unit, exclusion = build_evidence_unit(common, "article", text, "제1조", content_type="main_article")
            self.assertTrue(unit["embedding_candidate"])
            self.assertIsNone(exclusion)

    def test_pdf_title_body_is_excluded_while_table_and_conditions_remain(self):
        def page(body):
            return {"page_number": 1, "body_text": body, "images": [], "removed_margin_lines": [],
                    "tables": [{"rows": [["품목", "내용연수"], ["예시", "5년"]], "bbox": [0, 0, 1, 1]}]}
        path = Path("consumer_sources/table.pdf")
        entries = list(build_pdf_entries(path, [page("<별표 Ⅳ>\n품목별 내용연수표")]))
        self.assertEqual(entries[0]["reason"], "heading_only")
        self.assertIsNone(entries[1]["reason"])
        self.assertIn("5년", entries[1]["text"])
        entries = list(build_pdf_entries(path, [page("<별표 Ⅳ>\n다만, 적용 조건을 확인한다.")]))
        self.assertIsNone(entries[0]["reason"])
        self.assertIsNone(entries[1]["reason"])

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

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "Optional ingestion dependency")
    def test_case_page_metadata_is_separate_from_case_content(self):
        source = '''<div class="board_view"><table class="v_tbl resp_tb">
        <caption>이표는 수정일, 조회수, 질문, 답변의 항목으로 구성되어 있습니다.</caption>
        <tr class="web"><th>수정일</th><td>2025-02-13</td><th>조회수</th><td>2977</td></tr>
        <tr class="mobile"><th>수정일</th><td>2025-02-13</td></tr>
        <tr class="mobile"><th>조회수</th><td>2977</td></tr>
        <tr class="v_mobchg"><th>사건개요</th><td>수정일 2025-02-13에 조회수 2977을 확인했다.</td></tr>
        <tr><th>판단</th><td>환급 조건과 예외를 보존한다.</td></tr></table>
        <table><caption>환급 금액 기준</caption><tr><th>조회수</th><td>본문 표</td></tr></table></div>'''
        text, meta = html_extract(source.encode(), "kca_case_fixture.html")
        self.assertNotIn("항목으로 구성되어 있습니다", text)
        self.assertIn("수정일 2025-02-13에 조회수 2977을 확인했다.", text)
        self.assertIn("환급 조건과 예외를 보존한다.", text)
        self.assertIn("환급 금액 기준", text)
        self.assertEqual(meta["page_metadata"], {"수정일": ["2025-02-13"], "조회수": ["2977"]})
        self.assertEqual(len(meta["tables"][0]["rows"]), 2)
        # 다른 사이트의 같은 이름 필드는 이 규칙으로 제거하지 않는다.
        other = source.replace('class="board_view"', 'id="contents"')
        text, meta = html_extract(other.encode(), "other.html")
        self.assertIn("항목으로 구성되어 있습니다", text)
        self.assertNotIn("page_metadata", meta)

    def test_hwpx_text_runs_join_and_table_spans_remain(self):
        xml = '<section><p><run><t>계약</t></run><run><t>해지</t></run></p><tbl><tr><tc><cellSpan rowSpan="2" colSpan="1"/><p><t>전액 환급</t></p></tc></tr></tbl></section>'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "terms.hwpx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Contents/section0.xml", xml)
            text, meta = hwpx_extract(path)
        self.assertIn("계약해지", text)
        self.assertEqual(meta["tables"][0]["rows"][0][0]["spans"][0]["rowSpan"], "2")

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "Optional ingestion dependency")
    def test_real_case_content_survives_page_metadata_removal(self):
        from lxml import html
        from src.scripts.preprocess_data import clean, decode_html

        files = sorted(path for path in (ROOT / "data/raw/consumer_sources").glob("kca_*.html")
                       if path.name.startswith(("kca_case_", "kca_pilates_")))
        if not files:
            self.skipTest("Optional source-data regression check")
        checked_rows = 0
        for path in files:
            with self.subTest(source=path.name):
                data = path.read_bytes()
                text, meta = html_extract(data, path.name)
                tree = html.fromstring(decode_html(data)[0])
                for row in tree.xpath('//tr[contains(concat(" ",normalize-space(@class)," ")," v_mobchg ")]'):
                    for cell in row.xpath('./th|./td'):
                        expected = re.sub(r"\s+", "", clean(cell.text_content()))
                        self.assertIn(expected, re.sub(r"\s+", "", text))
                    checked_rows += 1
                self.assertIn("page_metadata", meta)
                self.assertNotIn("항목으로 구성되어 있습니다", text)
        self.assertGreater(checked_rows, 0)

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

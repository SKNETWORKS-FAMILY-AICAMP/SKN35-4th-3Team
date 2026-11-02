"""검토한 PDF 표의 열 추출과 페이지 간 행 연결."""

import hashlib
import re
import xml.etree.ElementTree as ET

from .data_io import ROOT

REVIEWED_PDF_NAME = "009338_288143_20260721_appendix_000100E.pdf"
REVIEWED_PDF_SHA256 = "7fee592cfcf1c56d4a8a49b49336ee51f21e46332f5a767000809e0dbc8b85e5"
REVIEWED_PDF_SOURCE = "data/raw/law_appendices/" + REVIEWED_PDF_NAME

REFUND_PDF = ROOT / "docs/data_reports/source_verification/consumer_dispute_standard_appendix_0002_159470209.pdf"
REFUND_PDF_SHA256 = "242401b8ae436e786a1b3c33aa5f568d7c8cb3a0927484a25ed933346bf72083"
REFUND_URL = "https://www.law.go.kr/LSW/flDownload.do?flSeq=159470209"
REFUND_XML_SHA256 = "64e922b28e0d2e2b79c275cf1acec392e97d60975226d383201511e1a9e9a7f1"


def extract_pdf_trial_page(page):
    # 페이지를 길게 가로지르는 실제 세로선만 열 경계로 사용한다.
    edges = sorted({round(edge["x0"], 3) for edge in page.edges
                    if edge.get("orientation") == "v"
                    and abs(edge["bottom"] - edge["top"]) > page.height / 2})
    if len(edges) < 2:
        return {"status": "not_enough_column_boundaries"}
    settings = {"vertical_strategy": "explicit", "explicit_vertical_lines": edges,
                "horizontal_strategy": "text", "intersection_x_tolerance": 20}
    trials = {}
    for label, options in (("default", {}), ("vertical_boundaries", settings)):
        tables = [table.extract() for table in page.find_tables(options)]
        trials[label] = {"tables": tables, "table_count": len(tables),
                         "row_count": sum(len(rows) for rows in tables),
                         "cell_chars": sum(len(cell or "") for rows in tables for row in rows for cell in row),
                         "column_counts": sorted({len(row) for rows in tables for row in rows})}
    return {"settings": settings, "trials": trials}


def trial_pdf_extraction():
    """문장 조각만 추출됐던 PDF 한 페이지에서 세로 경계 기반 추출을 비교한다."""
    import pdfplumber

    source = REVIEWED_PDF_SOURCE
    path = ROOT / source
    if not path.exists():
        return {"status": "sample_not_available"}
    with pdfplumber.open(path) as document:
        result = extract_pdf_trial_page(document.pages[1])
    return {"source_path": source, "page": 2, **result,
            "applied_to_search": False,
            "limitations": "Physical text lines, not complete logical rows. Cross-page conditions and headers are not reconstructed. More extracted text does not alone prove correctness."}


def group_pdf_rows(pages):
    """같은 열의 줄바꿈만 합친다. 시작·끝이 불명확한 조각은 따로 표시한다."""
    rows, orphan_fragments, pending = [], [], None
    previous_page = None
    for page_number, physical_rows in pages:
        if previous_page is not None and page_number != previous_page + 1:
            pending = None
        previous_page = page_number
        for index, cells in enumerate(physical_rows):
            if not any(cells):
                continue
            if len(cells) != 5:
                raise ValueError("Expected five columns in the reviewed table")
            label = re.match(r"^[가-힣]+(?:의\d+)?\.", cells[0] or "")
            if label:
                if pending:
                    pending["end_confirmed"] = True
                pending = {"label": label.group(), "cells": ["" for _ in cells],
                           "fragments": [], "end_confirmed": False}
                rows.append(pending)
            fragment = {"page": page_number, "physical_row": index, "cells": cells}
            if pending is None:
                orphan_fragments.append(fragment)
                continue
            pending["fragments"].append(fragment)
            for column, text in enumerate(cells):
                if text:
                    pending["cells"][column] = (pending["cells"][column] + " " + text).strip()
    return rows, orphan_fragments


def validate_pdf_extraction(path=None):
    """원본 화면으로 확인한 열 값과 페이지를 걸친 행 연결을 검증한다."""
    import pdfplumber

    source = REVIEWED_PDF_SOURCE
    path = path or ROOT / source
    if not path.exists():
        return {"status": "sample_not_available"}
    pages, trials = [], []
    with pdfplumber.open(path) as document:
        for number in (2, 3):
            result = extract_pdf_trial_page(document.pages[number - 1])
            if "trials" not in result:
                return {"status": "column_detection_failed", "page": number}
            tables = result["trials"]["vertical_boundaries"]["tables"]
            if len(tables) != 1:
                return {"status": "ambiguous_tables", "page": number}
            pages.append((number, tables[0]))
            trials.append({"page": number, **result})
    rows, orphans = group_pdf_rows(pages)
    # 원본 2~3페이지에서 눈으로 대조한 표본이다. 검색 정답에는 사용하지 않는다.
    expected = {"마.": ["1개월", "3개월", "6개월"],
                "바.": ["3개월", "6개월", "12개월"],
                "하.": ["1개월", "3개월", "6개월"],
                "버.": ["3개월", "6개월", "12개월"]}
    by_label = {row["label"]: row for row in rows}
    checks = []
    for label, months in expected.items():
        row = by_label.get(label)
        actual = [re.findall(r"\d+개월", cell) for cell in row["cells"][-3:]] if row else []
        checks.append({"label": label, "expected_months": months, "actual": actual,
                       "passed": bool(row and row["end_confirmed"] and actual == [[m] for m in months])})
    cross_page = by_label.get("하.")
    cross_page_passed = bool(cross_page and {f["page"] for f in cross_page["fragments"]} == {2, 3}
                             and "신고를 하지 않은" in cross_page["cells"][0])
    return {"source_path": source, "pages": [2, 3], "applied_to_production": False,
            "trials": trials, "logical_rows": rows, "orphan_fragments": orphans,
            "manual_sample_checks": checks, "cross_page_sample_passed": cross_page_passed,
            "all_sample_checks_passed": all(c["passed"] for c in checks) and cross_page_passed,
            "limitations": "Only the reviewed five-column table on two consecutive pages. Leading fragments and unconfirmed final rows are not complete evidence. Shared conditions on page 1 remain required; no general PDF accuracy claim."}


def reviewed_pdf_entries(path, pages):
    """원본 해시와 표본 검증이 일치하는 네 행만 공통 기준과 함께 구성한다."""
    if path.name != REVIEWED_PDF_NAME:
        return []
    if hashlib.sha256(path.read_bytes()).hexdigest() != REVIEWED_PDF_SHA256:
        raise ValueError("Reviewed PDF changed; manual review is required before extraction")
    result = validate_pdf_extraction(path)
    if not result.get("all_sample_checks_passed"):
        raise ValueError("Reviewed PDF row checks failed")
    first_page = pages[0]["raw_text"]
    boundary = re.search(r"(?m)^2\.\s*개별기준\s*$", first_page)
    if not boundary or "1. 일반기준" not in first_page[:boundary.start()]:
        raise ValueError("Common conditions were not found on the reviewed PDF first page")
    conditions = first_page[:boundary.start()].strip()
    reviewed_labels = {check["label"] for check in result["manual_sample_checks"] if check["passed"]}
    entries = []
    for row in result["logical_rows"]:
        if row["label"] not in reviewed_labels:
            continue
        source_pages = sorted({fragment["page"] for fragment in row["fragments"]})
        table_text = "위반행위 | 근거 법조문 | 1차 | 2차 | 3차\n" + " | ".join(row["cells"])
        entries.append({"key": "reviewed_row:" + row["label"],
                        "text": conditions + "\n\n2. 개별기준\n" + table_text,
                        "locator": "PDF " + ",".join(map(str, source_pages)) + "페이지 / 검증한 표 행 " + row["label"],
                        "content_type": "reviewed_pdf_table_row", "page_numbers": [1] + source_pages,
                        "calculation_restricted": True,
                        "evidence_metadata": {"reviewed_sha256": REVIEWED_PDF_SHA256,
                                              "fragments": row["fragments"],
                                              "common_conditions_page": 1,
                                              "common_conditions_text": conditions},
                        "warnings": ["검증한 네 행에만 적용; 공통기준을 포함한 전체 근거와 함께 확인"]})
    if len(entries) != 4:
        raise ValueError("Expected four reviewed PDF rows")
    return entries


def refund_ratio_text(basis):
    """PDF와 대조한 기간·횟수 분수 표현을 한 곳에서 관리한다."""
    if basis == "period":
        return "이미 경과한 기간(일수) / 계약상 이용기간(일수)"
    return "이미 이용한 횟수 / 계약상 이용횟수"


def fraction_pairs(numerators, denominators):
    """분자·분모의 위아래 위치와 열의 겹침을 확인한다."""
    pairs, used = [], set()
    for numerator in sorted(numerators, key=lambda item: item["top"]):
        candidates = []
        for index, denominator in enumerate(denominators):
            width = min(numerator["x1"] - numerator["x0"], denominator["x1"] - denominator["x0"])
            overlap = min(numerator["x1"], denominator["x1"]) - max(numerator["x0"], denominator["x0"])
            gap = denominator["top"] - numerator["bottom"]
            if index not in used and width > 0 and overlap / width >= .8 and 0 < gap < 30:
                candidates.append((gap, index, denominator))
        if not candidates:
            raise ValueError("Fraction denominator could not be identified from PDF geometry")
        _, index, denominator = min(candidates, key=lambda item: item[0])
        used.add(index)
        pairs.append((numerator, denominator))
    if len(used) != len(denominators):
        raise ValueError("Unmatched fraction denominators")
    return pairs


def load_verified_refund_source(units, *, raw_dir=None):
    """검토한 XML·PDF 버전과 보완 전 본문을 확인한다."""
    from .preprocess_data import clean

    if not REFUND_PDF.exists():
        raise FileNotFoundError("Verification PDF is missing; use the attachment referenced by the saved XML")
    if hashlib.sha256(REFUND_PDF.read_bytes()).hexdigest() != REFUND_PDF_SHA256:
        raise ValueError("Verification PDF differs from the visually reviewed version")
    unit = next(u for u in units if u["unit_id"] == "consumer_dispute_standard:industry:fitness_refund")
    xml_path = (raw_dir or ROOT / "data/raw") / unit["source_path"].removeprefix("data/raw/")
    if hashlib.sha256(xml_path.read_bytes()).hexdigest() != REFUND_XML_SHA256 or unit["raw_sha256"] != REFUND_XML_SHA256:
        raise ValueError("Refund XML differs from the visually reviewed version")
    tree = ET.parse(xml_path)
    appendix = next(a for a in tree.findall(".//별표단위") if a.findtext("별표번호") == "0002")
    if appendix.findtext("별표서식PDF파일링크") != "/LSW/flDownload.do?flSeq=159470209":
        raise ValueError("PDF link no longer matches the saved XML appendix")
    body = appendix.findtext("별표내용")
    source_text = clean(body[unit["source_start"]:unit["source_end"]])
    supplement = unit.get("evidence_metadata", {}).get("formula_supplement", {})
    if source_text != supplement.get("original_text", unit["text"]):
        raise ValueError("Preserved refund parent does not match its raw XML span")
    solution = "".join(line.split("│")[1] for line in source_text.splitlines() if "│" in line)
    missing_count = len(re.findall(r"이용료×\)", re.sub(r"\s+", "", solution)))
    return unit, source_text, supplement, missing_count


def extract_refund_formula_checks(page):
    """계산식의 분수 위치와 부호를 확인해 출처 좌표와 함께 반환한다."""
    checked = []
    business = page.search("사업자의 책임있는 사유로 인한 계약해제")
    consumer = page.search("소비자의 책임있는 사유로 인한 계약해제")
    if len(business) != 1 or len(consumer) != 1 or business[0]["top"] >= consumer[0]["top"]:
        raise ValueError("Responsibility headings were not found in the expected order")
    for basis, numerator_term, denominator_term in (
        ("period", "이미경과한기간", "계약상이용기간"),
        ("count", "이미이용한횟수", "계약상이용횟수"),
    ):
        pairs = fraction_pairs(page.search(numerator_term), page.search(denominator_term))
        if len(pairs) != 2:
            raise ValueError("Expected two visually reviewed fractions for each contract basis")
        for numerator, denominator in pairs:
            responsibility = "business" if numerator["top"] < consumer[0]["top"] else "consumer"
            if numerator["top"] < business[0]["top"]:
                raise ValueError("Fraction appears outside the reviewed cancellation conditions")
            sign = "+" if responsibility == "business" else "-"
            area = (300, numerator["top"] - 26, 435, denominator["bottom"] + 22)
            excerpt = page.crop(area).extract_text() or ""
            if sign not in excerpt:
                raise ValueError("Formula sign could not be confirmed in the reviewed crop")
            ratio = refund_ratio_text(basis)
            checked.append({"responsibility": responsibility, "contract_basis": basis,
                            "timing": "after_start", "page": 101,
                            "normalized_transcription": f"반환금액 = 이용료 - (이용료 × ({ratio})) {sign} 위약금",
                            "numerator_bbox": {k: numerator[k] for k in ("x0", "top", "x1", "bottom")},
                            "denominator_bbox": {k: denominator[k] for k in ("x0", "top", "x1", "bottom")},
                            "crop_text": excerpt, "visually_reviewed": True,
                            "calculation_enabled": False, "applied_to_processed": False})
    return checked


def check_refund_scope_notes(document):
    """계산식과 함께 필요한 적용 범위·예외 문구를 확인한다."""
    page = document.pages[100]
    notes_101 = page.crop((435, 0, page.width, page.height)).extract_text() or ""
    notes_102 = document.pages[101].extract_text() or ""
    compact = re.sub(r"\s+", "", notes_101 + notes_102)
    notes_checks = {
        "membership_facility_exclusion_present": "회원제체육시설업은제외" in compact,
        "deposit_excluded_from_fee_present": "보증금은이용료에포함되지않음" in compact,
        "penalty_one_tenth_present": "위약금은이용료의1/10" in compact,
        "gift_return_notes_present": "사은품반환" in compact,
        "yoga_pilates_scope_note_present": "체육시설업에는포함되지않음" in compact,
    }
    return notes_checks


def build_refund_verification_result(unit, source_text, supplement, missing_count, checked, notes_checks):
    """보완 전후 확인 상태와 출처를 기존 보고서 형식으로 구성한다."""
    applied = bool(supplement)
    if applied:
        expected, _ = supplement_formula_text(source_text, checked)
        if unit["text"] != expected:
            raise ValueError("Stored refund text does not match the verified supplement")
        for formula in checked:
            formula["applied_to_processed"] = True
    return {"xml_source": unit["source_path"], "xml_sha256": unit["raw_sha256"],
            "pdf_path": REFUND_PDF.relative_to(ROOT).as_posix(), "pdf_sha256": REFUND_PDF_SHA256,
            "official_url": REFUND_URL, "reference_matches_saved_xml": True,
            "raw_xml_matches_processed": source_text == unit["text"],
            "raw_xml_matches_preserved_original": True,
            "missing_fractions_in_xml": missing_count, "verified_formulas": checked,
            "context_checks": notes_checks, "context_pages": [101, 102],
            "sign_review": "Final plus/minus assignments were visually reviewed on the pinned PDF; crop checks alone are not a symbolic formula parser.",
            "status": "applied_with_pdf_provenance" if applied else "verified_for_review_not_promoted",
            "limitations": "Visual transcription and geometry verification of the archived attachment; not current legal advice or a refund calculator. Scope notes remain required."}


def verify_refund_formulas(units, *, raw_dir=None):
    """원본 검증, 계산식 확인, 결과 구성을 순서대로 실행한다."""
    import pdfplumber

    unit, source_text, supplement, missing_count = load_verified_refund_source(units, raw_dir=raw_dir)
    with pdfplumber.open(REFUND_PDF) as document:
        checked = extract_refund_formula_checks(document.pages[100])
        notes_checks = check_refund_scope_notes(document)
    if len(checked) != 4 or missing_count != 4 or not all(notes_checks.values()):
        raise ValueError("Refund source checks did not match the reviewed sample")
    return build_refund_verification_result(unit, source_text, supplement, missing_count, checked, notes_checks)


def supplement_formula_text(original, formulas):
    """검증한 문서 순서대로 빈 분수만 보완하고 원본 기준 수정 구간을 반환한다."""
    formulas = sorted(formulas, key=lambda formula: formula["numerator_bbox"]["top"])
    expected_order = [("business", "period"), ("business", "count"),
                      ("consumer", "period"), ("consumer", "count")]
    if [(f["responsibility"], f["contract_basis"]) for f in formulas] != expected_order:
        raise ValueError("Verified formula order differs from the reviewed XML table")
    positions = list(re.finditer("이용료×", original))
    if len(positions) != 4:
        raise ValueError("Expected four XML formula placeholders")
    parts, corrections, cursor, delta = [], [], 0, 0
    for match, formula in zip(positions, formulas):
        ratio = refund_ratio_text(formula["contract_basis"])
        replacement = "이용료×(" + ratio + ")"
        parts.extend([original[cursor:match.start()], replacement])
        corrections.append({"original_start": match.start(), "original_end": match.end(),
                            "processed_start": match.start() + delta,
                            "processed_end": match.start() + delta + len(replacement),
                            "before": match.group(), "after": replacement, **formula})
        delta += len(replacement) - len(match.group())
        cursor = match.end()
    parts.append(original[cursor:])
    return "".join(parts), corrections


def apply_refund_formulas(unit, *, raw_dir=None):
    """검증한 환불표 한 개에만 보완과 출처를 기록한다. 자동 계산은 활성화하지 않는다."""
    if unit["unit_id"] != "consumer_dispute_standard:industry:fitness_refund":
        return unit
    result = verify_refund_formulas([unit], raw_dir=raw_dir)
    if unit.get("evidence_metadata", {}).get("formula_supplement"):
        return unit
    text, corrections = supplement_formula_text(unit["text"], result["verified_formulas"])
    for correction in corrections:
        correction["applied_to_processed"] = True
    attachment = {"path": result["pdf_path"], "sha256": result["pdf_sha256"],
                  "url": result["official_url"], "pages": result["context_pages"],
                  "role": "verified_refund_formula_supplement"}
    return {**unit, "text": text, "extraction_origin": "raw_xml_with_verified_pdf_supplement",
            "calculation_restricted": True,
            "text_offset_basis": "processed_text_with_verified_pdf_supplement",
            "supplemental_sources": [attachment],
            "evidence_metadata": {**unit.get("evidence_metadata", {}), "formula_supplement": {
                "original_text": unit["text"], "original_warnings": unit.get("warnings", []),
                "xml_source_path": unit["source_path"], "xml_sha256": unit["raw_sha256"],
                "xml_offsets_reference": "original_text", "pdf_source": attachment,
                "corrections": corrections, "calculation_enabled": False}},
            "warnings": ["계산식 네 개를 PDF와 대조함; 적용 범위와 예외는 전체 근거에서 확인. 자동 계산은 비활성화 상태."]}


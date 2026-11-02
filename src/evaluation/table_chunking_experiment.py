"""환불표를 조건별로 나누고 기존 청킹과 비교한다. 기존 데이터 파일은 수정하지 않는다.

실행: python -m src.evaluation.table_chunking_experiment
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET

from src.scripts.chunk_data import make_chunks, stats, validate
from src.scripts.data_io import ROOT, load_chunks, load_processed_data, read_json
from src.scripts.preprocess_data import clean
from .retrieval_check import evaluate


def condition_spans(text):
    """표의 첫 열에 있는 번호·이용 시점에서 나눈다. 원문 구간은 유지한다."""
    markers = [(0, "", [])]
    condition, condition_source = "", []
    for line in re.finditer(r"(?m)^.+$", text):
        cell = re.match(r"[┃│]\s*([^│┃]+)", line.group())
        if not cell:
            if line.group().startswith("【참고】"):
                condition, condition_source = "", []
                markers.append((line.start(), line.group().strip(),
                                [{"start": line.start(), "end": line.end()}]))
            continue
        label = cell.group(1).strip()
        source_start = line.start() + cell.start(1)
        source = {"start": source_start, "end": line.start() + cell.end(1)}
        if re.match(r"\d+\)", label):
            condition, condition_source = label, [source]
            markers.append((line.start(), condition, condition_source))
        elif re.match(r"-\s*이용개시일\s*(?:이전|이후)", label) and condition:
            markers.append((line.start(), condition + " / " + label, condition_source + [source]))
        elif re.match(r"[①-⑳]|ㅇ\s", label):
            condition, condition_source = "", []
            markers.append((line.start(), label, [source]))
    if not any(sources for _, _, sources in markers):
        return [(0, len(text), "", [])]
    return [(start, markers[i + 1][0] if i + 1 < len(markers) else len(text), label, sources)
            for i, (start, label, sources) in enumerate(markers)]


def make_condition_chunks(unit, size=900, overlap=100):
    """검색 문맥을 붙이되 본문·전체 근거·계산 제한은 유지한다."""
    if unit.get("content_type") != "industry_table":
        yield from make_chunks(unit, size, overlap)
        return
    for start, end, label, sources in condition_spans(unit["text"]):
        section = {**unit, "text": unit["text"][start:end]}
        for chunk in make_chunks(section, size, overlap):
            chunk["start"] += start
            chunk["end"] += start
            header = f"[{unit['title']} / {unit['locator']} / {label}]"[:350]
            chunk["embedding_text"] = header + "\n" + chunk["text"]
            chunk["condition_label"] = label
            chunk["condition_label_sources"] = sources
            chunk["split_basis"] = "table_condition_experiment"
            key = f"condition-v1:{unit['unit_id']}:{chunk['start']}:{chunk['end']}:{size}:{overlap}:{header}:{chunk['text']}"
            chunk["chunk_id"] = hashlib.sha256(key.encode()).hexdigest()[:24]
            yield chunk


def audit_tables(units):
    """저장된 표의 추출 상태를 점검한다. 빈칸은 추측해서 채우지 않는다."""
    suspicious = []
    table_count = 0
    for path in sorted((ROOT / "data/processed").rglob("*.json")):
        document = read_json(path)
        for page in document.get("pages", []):
            for index, table in enumerate(page["tables"]):
                table_count += 1
                cells = [str(cell).strip() for row in table["rows"] for cell in row if cell]
                if sum(len(cell) for cell in cells) < 40:
                    suspicious.append({"source_path": document["source_path"],
                                       "page": page["page_number"], "table_index": index,
                                       "text": " | ".join(cells),
                                       "reason": "short_table_requires_visual_review"})
    source_checks = []
    for unit in units:
        if unit.get("content_type") != "industry_table":
            continue
        tree = ET.parse(ROOT / unit["source_path"])
        body = next(a.findtext("별표내용") for a in tree.findall(".//별표단위")
                    if a.findtext("별표번호") == "0002")
        source = clean(body[unit["source_start"]:unit["source_end"]])
        solution = "".join(line.split("│")[1] for line in source.splitlines() if "│" in line)
        missing = len(re.findall(r"이용료×\)", re.sub(r"\s+", "", solution)))
        source_checks.append({"unit_id": unit["unit_id"], "source_path": unit["source_path"],
                              "raw_span_matches_processed": source == unit["text"],
                              "incomplete_formula_count_in_raw": missing,
                              "calculation_restricted": unit.get("calculation_restricted", False),
                              "pdf_cell_alignment_verified": False})
    return {"pdf_table_count": table_count, "short_table_candidates": suspicious,
            "refund_source_checks": source_checks,
            "limitations": "Short tables are review candidates, not proven errors. Refund XML formulas are not repaired; matching refund PDF still needs review."}


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

    source = "data/raw/law_appendices/009338_288143_20260721_appendix_000100E.pdf"
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


def validate_pdf_extraction():
    """원본 화면으로 확인한 열 값과 페이지를 걸친 행 연결을 검증한다."""
    import pdfplumber

    source = "data/raw/law_appendices/009338_288143_20260721_appendix_000100E.pdf"
    path = ROOT / source
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


def main():
    paths = sorted(p for p in (ROOT / "data").rglob("*") if p.is_file())
    before_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    units = load_processed_data()
    old = list(load_chunks())
    targets = {u["unit_id"]: u for u in units if u.get("content_type") == "industry_table"}
    new, seen = [], set()
    for chunk in old:
        parent = chunk["parent_id"]
        if parent not in targets:
            new.append(chunk)
        elif parent not in seen:
            new.extend(make_condition_chunks(targets[parent]))
            seen.add(parent)
    validate(units, new, 900)
    comparison = {}
    for mode in ("baseline", "enhanced"):
        before, after = evaluate(old, mode=mode), evaluate(new, mode=mode)
        comparison[mode] = {
            "before": {k: v for k, v in before.items() if k != "cases"},
            "after": {k: v for k, v in after.items() if k != "cases"},
            "rank_changes": [{"query": a["query"], "before": a["rank_at_5"], "after": b["rank_at_5"]}
                             for a, b in zip(before["cases"], after["cases"])
                             if a["rank_at_5"] != b["rank_at_5"]],
        }
    current_paths = {p for p in (ROOT / "data").rglob("*") if p.is_file()}
    assert current_paths == set(before_hashes)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before_hashes.items())
    report = {"experiment": "source_condition_boundaries", "applied_to_production": False,
              "data_files_unchanged": True, "target_units": sorted(targets),
              "before_chunks": stats(old), "after_chunks": stats(new),
              "validation": "parent offsets, complete eligible text coverage, size, unique IDs passed",
              "table_audit": audit_tables(units), "pdf_extraction_trial": trial_pdf_extraction(),
              "pdf_extraction_validation": validate_pdf_extraction(),
              "comparison": comparison,
              "experimental_chunks": [c for c in new if c["parent_id"] in targets],
              "limitations": "Existing 43 scored development questions and 1 unscored question; no held-out generalization claim. Shared notes and missing formulas require complete parent evidence."}
    path = ROOT / "docs/data_reports/table_chunking_experiment.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(path), "before_chunks": len(old), "after_chunks": len(new),
                      "comparison": comparison}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

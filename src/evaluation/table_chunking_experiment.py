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
from src.scripts.pdf_tables import (
    extract_pdf_trial_page, group_pdf_rows, trial_pdf_extraction, validate_pdf_extraction,
)


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
        supplement = unit.get("evidence_metadata", {}).get("formula_supplement", {})
        processed_solution = "".join(line.split("│")[1] for line in unit["text"].splitlines() if "│" in line)
        source_checks.append({"unit_id": unit["unit_id"], "source_path": unit["source_path"],
                              "raw_span_matches_processed": source == unit["text"],
                              "raw_span_matches_preserved_original": source == supplement.get("original_text", unit["text"]),
                              "incomplete_formula_count_in_raw": missing,
                              "incomplete_formula_count_in_processed": len(re.findall(r"이용료×\)", re.sub(r"\s+", "", processed_solution))),
                              "pdf_formula_alignment_verified": bool(supplement),
                              "calculation_restricted": unit.get("calculation_restricted", False),
                              "pdf_cell_alignment_verified": False})
    return {"pdf_table_count": table_count, "short_table_candidates": suspicious,
            "refund_source_checks": source_checks,
            "limitations": "Short tables are review candidates, not proven errors. Four reviewed fractions may be supplemented from the pinned PDF with provenance; other table layout claims still require review."}


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

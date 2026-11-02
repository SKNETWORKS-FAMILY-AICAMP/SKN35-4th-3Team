"""Validate existing extractions against raw files and build traceable evidence units.

This is a second preprocessing pass, not a PDF/HTML extraction engine.
Source text and manual-review gates are preserved; normalization happens in search only.
"""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def old_chunks(root=ROOT):
    for path in sorted((root / "data/chunk").rglob("*")):
        if path.suffix == ".jsonl":
            yield from (json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
        elif path.name.endswith("_chunks.json"):
            yield from read_json(path)["chunks"]


def validate_source_hashes(units, raw_dir):
    """Check evidence against raw files once per source, using a configurable root."""
    raw_dir = Path(raw_dir).resolve()
    expected_hashes = {}
    for unit in units:
        relative = Path(unit["source_path"])
        if relative.parts[:2] != ("data", "raw"):
            raise ValueError(f"Invalid raw source path: {relative}")
        path = (raw_dir / Path(*relative.parts[2:])).resolve()
        if not path.is_relative_to(raw_dir):
            raise ValueError("Raw source path escapes its directory")
        expected = unit["raw_sha256"]
        if path in expected_hashes and expected_hashes[path] != expected:
            raise ValueError(f"Inconsistent source hashes: {relative}")
        expected_hashes[path] = expected
    for path, expected in expected_hashes.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Raw source changed: {path}")


def load_processed_data(processed_dir=None, *, raw_dir=None, verify_sources=True):
    """Load eligible evidence for chunking without changing any files."""
    from .data_layout import iter_processed_units
    processed_dir = Path(processed_dir or ROOT / "data/processed").resolve()
    if not processed_dir.is_dir():
        raise FileNotFoundError(f"Processed directory does not exist: {processed_dir}")
    units = list(iter_processed_units(processed_dir))
    if not units:
        raise ValueError(f"No eligible processed evidence found: {processed_dir}")
    ids = [u["unit_id"] for u in units]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate processed evidence IDs")
    if verify_sources:
        validate_source_hashes(units, raw_dir or processed_dir.parent / "raw")
    return units


def build_units(root=ROOT):
    """Return complete parent evidence and exclusions without changing source data."""
    from .data_layout import read_processed_units
    grouped = list(read_processed_units(root / "data"))
    if grouped:
        validate_source_hashes(grouped, root / "data/raw")
        report_path = root / "docs/data_reports/quality_report.json"
        return grouped, read_json(report_path).get("exclusions", []) if report_path.exists() else []
    current = root / "data/processed/parents.jsonl"
    if current.exists():
        units = [json.loads(line) for line in current.read_text(encoding="utf-8").splitlines() if line.strip()]
        checked = set()
        for unit in units:
            path = (root / unit["source_path"]).resolve()
            if not path.is_relative_to((root / "data/raw").resolve()):
                raise ValueError("Invalid raw source path")
            if path not in checked:
                if hashlib.sha256(path.read_bytes()).hexdigest() != unit["raw_sha256"]:
                    raise ValueError(f"Raw source changed: {unit['source_path']}")
                checked.add(path)
        report_path = root / "data/processed/reports/quality_report.json"
        return units, read_json(report_path).get("exclusions", []) if report_path.exists() else []
    metadata = {}
    for chunk in old_chunks(root):
        key = chunk.get("source_id") or chunk.get("version_id") or chunk.get("guide_id")
        metadata.setdefault(key, chunk)
    units, excluded = [], []
    for kind in ("laws", "consumer_sources", "guides"):
        for path in sorted((root / "data/processed" / kind).glob("*.json")):
            doc = read_json(path)
            identity = doc.get("version_id") or doc.get("source_id") or doc.get("guide_id")
            if not identity:
                continue
            meta = metadata.get(identity, {})
            raw = doc.get("raw_file") or doc.get("source_path") or f"data/raw/{kind}/{doc['source_file']}"
            source = (root / raw).resolve()
            if not source.is_relative_to((root / "data/raw").resolve()):
                raise ValueError(f"Invalid raw path: {raw}")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != doc["raw_sha256"]:
                raise ValueError(f"Raw source changed: {raw}")
            common = {
                "document_id": identity, "document_type": doc.get("document_type", kind),
                "title": doc.get("law_name") or doc["title"], "source_path": raw,
                "processed_path": path.relative_to(root).as_posix(), "raw_sha256": digest,
                "source_url": doc.get("source_url"), "domains": meta.get("domains", []),
                "dates": meta.get("dates", {}), "warnings": doc.get("warnings", []),
            }

            def add(key, text, item, locator, **extra):
                reason = None
                if item.get("is_deleted"):
                    reason = "deleted"
                elif item.get("needs_manual_review") or item.get("visual_review_required") or item.get("ocr_required"):
                    reason = "manual_review_required"
                elif item.get("embedding_candidate") is False:
                    reason = "not_embedding_candidate"
                elif not text.strip():
                    reason = "empty_text"
                elif "\ufffd" in text or "(cid:" in text:
                    reason = "broken_characters"
                if reason:
                    excluded.append({"document_id": identity, "unit_id": key, "reason": reason, "locator": locator})
                    return
                units.append({**common, "unit_id": f"{identity}:{key}", "text": text,
                              "locator": locator, "warnings": common["warnings"] + item.get("warnings", []),
                              "embedding_candidate": True, **extra})

            if kind == "laws":
                for item in doc["articles"] + doc.get("supplements", []) + doc.get("appendices", []):
                    key = item.get("article_id") or item["unit_id"]
                    locator = item.get("article_label", item.get("title", key))
                    if item.get("article_title"):
                        locator += f"({item['article_title']})"
                    add(key, item["body_text"], item, locator,
                        content_type=item.get("content_type", "main_article"),
                        article_number=item.get("article_number"),
                        effective_date=item.get("effective_date"),
                        tables=item.get("tables", []))
            elif kind == "consumer_sources":
                if not doc.get("embedding_candidate") or doc.get("needs_manual_review"):
                    excluded.append({"document_id": identity, "reason": "document_review_gate"})
                    continue
                for item in doc["units"]:
                    add(item["unit_id"], item["text"], item,
                        item.get("source_locator") or item["unit_id"], role=item["role"],
                        atomic=item.get("atomic", False),
                        # Retain all reviewed structured tables, formulas and restrictions.
                        evidence_metadata={k: v for k, v in item.items() if k != "text"},
                        document_constraints={k: v for k, v in doc.items() if k in (
                            "valid_from", "valid_until", "terms_revision_date", "law_version_review",
                            "version_role", "calculation_restricted_clause", "source_anomaly")})
            else:
                heading = ""
                for page in doc["pages"]:
                    number = page["page_number"]
                    if page.get("page_kind") != "content":
                        continue
                    if page.get("headings"):
                        heading = " > ".join(h["text"] for h in page["headings"])
                    add(f"p{number}:body", page.get("body_text", ""), page,
                        f"PDF {number}페이지 / {heading}", content_type="body", page_number=number)
                    for index, table in enumerate(page.get("tables", [])):
                        text = table.get("serialized_text") or "\n".join(
                            " | ".join(str(cell or "") for cell in row) for row in table.get("rows", []))
                        add(f"p{number}:table{index}", text, page,
                            f"PDF {number}페이지 / {heading} / 표 {index + 1}",
                            content_type="table", page_number=number, table=table)
                    for index, summary in enumerate(page.get("visual_summaries", [])):
                        add(f"p{number}:visual{index}", summary.get("summary", ""), page,
                            f"PDF {number}페이지 / 검토된 이미지 요약 {index + 1}",
                            content_type="visual_summary", page_number=number,
                            text_origin="manual_visual_summary")
    return units, excluded

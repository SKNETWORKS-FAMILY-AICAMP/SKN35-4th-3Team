"""Bounded retrieval children with lossless, complete parent evidence.

Run: python -m src.scripts.chunk_data
This entry point reads processed data only. No API keys are required.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re

from .data_io import ROOT, load_processed_data


def validate_chunk_options(size, overlap):
    """청크 크기와 중복 길이가 허용 범위인지 확인한다."""
    if type(size) is not int or type(overlap) is not int or size < 100 or not 0 <= overlap < size // 2:
        raise ValueError("size >= 100 and 0 <= overlap < size / 2 are required")


def split_spans(text, size=900, overlap=100):
    """문단·문장 경계를 우선하여 원문 구간을 나눈다."""
    validate_chunk_options(size, overlap)
    boundaries = sorted({m.end() for m in re.finditer(r"\n+|[.!?。](?:\s+|$)|다\.(?:\s+|$)", text)} | {len(text)})
    start = 0
    while start < len(text):
        limit = min(start + size, len(text))
        candidates = [b for b in boundaries if start + size // 2 <= b <= limit]
        end = len(text) if limit == len(text) else max(candidates, default=limit)
        if text[start:end].strip():
            yield start, end
        if end == len(text):
            break
        # 경계에 걸친 조건이나 예외를 놓치지 않도록 일부 구간을 겹친다.
        start = max(start + 1, end - overlap)


def make_chunks(unit, size=900, overlap=100):
    header = f"[{unit['title']} / {unit['locator']}]"
    # 긴 제목은 검색 입력에서만 줄이고 원래 위치 정보는 유지한다.
    header = header[:350]
    for start, end in split_spans(unit["text"], size, overlap):
        text = unit["text"][start:end]
        key = f"v2:{unit['unit_id']}:{start}:{end}:{size}:{overlap}:{text}"
        metadata = {k: v for k, v in unit.items() if k not in (
            "text", "tables", "table", "evidence_metadata", "document_constraints")}
        yield {**metadata, "schema_version": 2,
               "chunk_id": hashlib.sha256(key.encode()).hexdigest()[:24],
               "parent_id": unit["unit_id"], "start": start, "end": end,
               "offset_basis": "parent.text", "text": text,
               "embedding_text": header + "\n" + text,
               "requires_parent_evidence": True,
               "retrieval_only": True,
               "split_basis": "boundary_window_with_parent_evidence"}


def stats(rows):
    lengths = sorted(len(r["text"]) for r in rows)
    embeddings = [len(r.get("embedding_text", r["text"])) for r in rows]
    return {"count": len(rows), "min_chars": min(lengths, default=0),
            "median_chars": lengths[len(lengths) // 2] if lengths else 0,
            "p95_chars": lengths[int(len(lengths) * .95)] if lengths else 0,
            "max_chars": max(lengths, default=0), "under_100_chars": sum(n < 100 for n in lengths),
            "over_2000_chars": sum(n > 2000 for n in lengths),
            "max_embedding_chars": max(embeddings, default=0)}


def validate(units, chunks, size):
    parents = {unit["unit_id"]: unit for unit in units}
    seen, covered = set(), {}
    for chunk in chunks:
        if chunk["chunk_id"] in seen:
            raise ValueError("Duplicate chunk ID")
        seen.add(chunk["chunk_id"])
        parent = parents[chunk["parent_id"]]
        if chunk["text"] != parent["text"][chunk["start"]:chunk["end"]]:
            raise ValueError("Source offset mismatch")
        if len(chunk["text"]) > size or len(chunk["embedding_text"]) > size + 351:
            raise ValueError("Chunk size limit exceeded")
        covered.setdefault(chunk["parent_id"], []).append((chunk["start"], chunk["end"]))
    for key, parent in parents.items():
        cursor = 0
        for start, end in sorted(covered.get(key, [])):
            if parent["text"][cursor:start].strip():
                raise ValueError(f"Uncovered source span: {key}")
            cursor = max(cursor, end)
        if parent["text"][cursor:].strip():
            raise ValueError(f"Uncovered source tail: {key}")


def chunk_data(processed_dir=None, chunk_dir=None, *, chunk_size=900, overlap=100,
               raw_dir=None, root=ROOT, report_dir=None):
    """Read processed documents, validate sources, and save retrieval chunks only.

    Return chunk_count, parent_count, saved file paths, and length statistics.
    This function never extracts raw files or rewrites processed documents.
    """
    from .data_io import save_chunks
    validate_chunk_options(chunk_size, overlap)
    root = Path(root).resolve()
    processed_dir = Path(processed_dir or root / "data/processed").resolve()
    chunk_dir = Path(chunk_dir or root / "data/chunk").resolve()
    raw_dir = Path(raw_dir or root / "data/raw").resolve()
    report_dir = Path(report_dir or root / "docs/data_reports").resolve()
    for protected in (processed_dir, raw_dir):
        if chunk_dir == protected or chunk_dir.is_relative_to(protected) or protected.is_relative_to(chunk_dir):
            raise ValueError("Chunk directory must not overlap raw or processed directories")
    units = load_processed_data(processed_dir, raw_dir=raw_dir)
    chunks = [chunk for unit in units for chunk in make_chunks(unit, chunk_size, overlap)]
    validate(units, chunks, chunk_size)
    saved = save_chunks(chunk_dir, chunks)
    report = {"raw_reextraction_performed": False,
              "chunk_size": chunk_size, "overlap": overlap,
              "parent_count": len(units), "chunk_count": len(chunks),
              "new": stats(chunks), "saved_files": [str(path) for path in saved],
              "validation": "raw_hashes, unique_ids, offsets, full_text_coverage, size_limits passed"}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "chunking_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Chunk processed documents only; preprocessing is a separate command.")
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--chunk-dir", type=Path)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=100)
    args = parser.parse_args()
    report = chunk_data(args.processed_dir, args.chunk_dir, chunk_size=args.size,
                        overlap=args.overlap, raw_dir=args.raw_dir, report_dir=args.report_dir)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

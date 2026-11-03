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

# 본문 900자 제한과 별개로, 검색 입력에 덧붙이는 문맥의 길이를 제한한다.
MAX_CONTEXT_CHARS = 700
MAX_HEADER_CHARS = 350
SEPARATOR_CHARS = 1  # 제목·추가 문맥·본문 사이의 줄바꿈 한 글자
REVIEWED_LONG_ARTICLES = {
    "009318_282793_20260721:article19",  # 청약철회등
    "009318_282793_20260721:article20",  # 청약철회등의 효과
    "001248_276291_20260102:article4",   # 임차권등기명령
}
PARAGRAPH_PATTERN = re.compile(r"(?m)^[①-⑳㉑-㉟㊱-㊿]")
ITEM_PATTERN = re.compile(r"(?m)^\d+\.\s+")


def split_legal_spans(text, size=900, overlap=100):
    """항을 우선 유지하고, 긴 항만 호·문장 단위로 나눈다."""
    validate_chunk_options(size, overlap)
    blocks = legal_article_blocks(text, size, overlap)
    yield from pack_legal_blocks(text, blocks, size, overlap)


def legal_article_blocks(text, size, overlap):
    """항·호를 기본 구간으로 만들고, 너무 긴 구간만 문장 경계로 나눈다."""
    paragraphs = list(PARAGRAPH_PATTERN.finditer(text))
    # 조문 제목은 첫 항과 함께 둔다.
    starts = [0] + [match.start() for match in paragraphs[1:]]
    blocks = []
    for left, right in zip(starts, starts[1:] + [len(text)]):
        if right - left <= size:
            blocks.append((left, right))
            continue
        items = [left + match.start() for match in ITEM_PATTERN.finditer(text[left:right])]
        edges = sorted({left, right, *items})
        for begin, finish in zip(edges, edges[1:]):
            if finish - begin <= size:
                blocks.append((begin, finish))
            else:
                # 부분 문자열에서 얻은 위치를 부모 조문 전체의 문자 위치로 되돌린다.
                blocks.extend((begin + a, begin + b)
                              for a, b in split_spans(text[begin:finish], size, overlap))
    return blocks


def pack_legal_blocks(text, blocks, size, overlap):
    """기본 구간을 크기 제한 안에서 묶고 완전한 구간만 겹친다."""
    if not blocks:
        return
    start, end = blocks[0]
    last_start = start
    for left, right in blocks[1:]:
        if right - start > size:
            if text[start:end].strip():
                yield start, end
            # 임의의 100자를 자르지 않고, 겹칠 수 있는 완전한 항·호만 반복한다.
            start = last_start if (end - last_start <= overlap and last_start > start
                                   and right - last_start <= size) else left
        last_start, end = left, right
    if text[start:end].strip():
        yield start, end


def legal_article_context(unit, start, end):
    """긴 항의 중간부터 시작하는 청크에 소속 항·호의 원문을 연결한다."""
    text = unit["text"]
    paragraphs = list(PARAGRAPH_PATTERN.finditer(text))
    labels, sources = [], []
    for index, paragraph in enumerate(paragraphs):
        left = paragraph.start()
        right = paragraphs[index + 1].start() if index + 1 < len(paragraphs) else len(text)
        if not left < start < right:
            continue
        # 항의 처음부터 담긴 청크에는 도입부를 중복해서 붙이지 않는다.
        items = list(ITEM_PATTERN.finditer(text[left:right]))
        intro_end = left + items[0].start() if items else right
        spans = [(left, intro_end, "이어지는 항의 도입부")]
        for i, item in enumerate(items):
            item_start = left + item.start()
            item_end = left + items[i + 1].start() if i + 1 < len(items) else right
            if item_start < start < item_end:
                spans.append((item_start, item_end, "이어지는 호"))
        for begin, finish, label in spans:
            while finish > begin and text[finish - 1].isspace():
                finish -= 1
            # 조건문을 임의로 잘라 복사하지 않는다. 긴 문맥은 소속 번호만 표시한다.
            if sum(len(part) + 1 for part in labels) + len(label) + finish - begin + 2 > MAX_CONTEXT_CHARS:
                marker = PARAGRAPH_PATTERN.match(text, begin) or ITEM_PATTERN.match(text, begin)
                if marker is None:
                    continue
                finish = marker.end()
                label += " 번호 (조건·예외는 부모 근거 전체 확인)"
            source = {"start": begin, "end": finish, "text": text[begin:finish]}
            sources.append(source)
            labels.append(label + ": " + source["text"])
    context = "\n".join(labels)
    if len(context) > MAX_CONTEXT_CHARS:
        raise ValueError("Legal article context exceeds size limit")
    return context, sources


def uses_legal_structure(unit, size):
    return (unit["unit_id"] in REVIEWED_LONG_ARTICLES
            and unit.get("content_type") == "main_article" and len(unit["text"]) > size)


def retrieval_context(unit, start, end, size):
    """검토된 조문·환불표에만 추가 문맥을 구성하고 나머지는 그대로 둔다."""
    if uses_legal_structure(unit, size):
        return legal_article_context(unit, start, end)
    return refund_table_context(unit, start, end)


def refund_table_sections(text):
    """검토한 환불표의 책임 구간·표 끝·참고 제목을 찾는다."""
    # 표의 오른쪽 비고에 등장하는 같은 표현을 조건 제목으로 오인하지 않는다.
    roles = list(re.finditer(r"(?m)^┃\s*([45]\) [^│\n]+?)\s*│", text))
    if len(roles) != 2 or not roles[0].group(1).startswith("4)") or not roles[1].group(1).startswith("5)"):
        raise ValueError("Reviewed refund table headings changed")
    table_end = text.index("┗", roles[1].end())
    reference = re.search(r"(?m)^(【참고】[^\n]+)", text)
    if reference is None:
        raise ValueError("Reviewed refund table reference heading missing")
    return roles, table_end, reference


def refund_column_spans(text, start, end, reference_start):
    """청크에 해당하는 표의 열 제목과 원문 위치를 반환한다."""
    spans = []
    for header in re.finditer(r"(?m)^[┃│]([^\n]*?)\s*[┃│]$", text):
        compact = re.sub(r"\s+", "", header.group(1))
        if compact == "분쟁유형│해결기준│비고" and start < reference_start:
            if not any(label.startswith("표의 열:") for _, _, label in spans):
                spans.append((header.start(1), header.end(1), "표의 열: " + compact.replace("│", " / ")))
        elif compact == "업종│위약금기준" and end > reference_start:
            spans.append((header.start(1), header.end(1), "참고 표의 열: " + compact.replace("│", " / ")))
    return spans


def refund_condition_spans(text, roles, table_end, start, end):
    """청크와 겹치는 책임·이용 시점만 골라 부모 본문 위치로 반환한다."""
    spans = []
    for index, role in enumerate(roles):
        section_end = roles[index + 1].start() if index + 1 < len(roles) else table_end
        if start >= section_end or end <= role.start():
            continue
        spans.append((role.start(1), role.end(1), "포함 구간: " + role.group(1)))
        timings = list(re.finditer(r"(?m)^┃\s*-\s*(이용개시일 (?:이전|이후))\s*│",
                                   text[role.end():section_end]))
        for i, timing in enumerate(timings):
            timing_start = role.end() + timing.start()
            timing_end = role.end() + timings[i + 1].start() if i + 1 < len(timings) else section_end
            if start < timing_end and end > timing_start:
                # 잘라 읽은 구간의 위치를 다시 부모 환불표 기준으로 저장한다.
                left, right = role.end() + timing.start(1), role.end() + timing.end(1)
                spans.append((left, right, "  적용 시점: " + text[left:right]))
    return spans


def refund_table_context(unit, start, end):
    """체육시설업 표의 겹치는 구간에만 원문 제목과 조건을 연결한다."""
    if unit["unit_id"] != "consumer_dispute_standard:industry:fitness_refund":
        return "", []
    text = unit["text"]
    roles, table_end, reference = refund_table_sections(text)
    spans = refund_column_spans(text, start, end, reference.start())
    spans.extend(refund_condition_spans(text, roles, table_end, start, end))
    gift = re.search(r"(?m)^┃(ㅇ 사은품 반환)\s*┃", text)
    if gift is not None:
        gift_end = text.index("┃ㅇ 체육시설업", gift.end())
        if start < gift_end and end > gift.start():
            spans.append((gift.start(1), gift.end(1), "포함 구간: " + gift.group(1)))
    if end > reference.start():
        spans.append((reference.start(1), reference.end(1), "포함 구간: " + reference.group(1)))
    # 표시 문구와 원문 위치는 같은 구간 목록에서 만들어 서로 어긋나지 않게 한다.
    context = "\n".join(label for _, _, label in spans)
    sources = [{"start": left, "end": right, "text": text[left:right]} for left, right, _ in spans]
    if len(context) > MAX_CONTEXT_CHARS:
        raise ValueError("Refund table context exceeds size limit")
    return context, sources


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
    """부모 본문의 정확한 구간과, 제목·조건을 덧붙인 검색 입력을 함께 만든다."""
    header = f"[{unit['title']} / {unit['locator']}]"
    # 긴 제목은 검색 입력에서만 줄이고 원래 위치 정보는 유지한다.
    header = header[:MAX_HEADER_CHARS]
    structured = uses_legal_structure(unit, size)
    spans = split_legal_spans(unit["text"], size, overlap) if structured else split_spans(unit["text"], size, overlap)
    for start, end in spans:
        text = unit["text"][start:end]
        context, context_sources = retrieval_context(unit, start, end, size)
        context_metadata = ({"retrieval_context": context, "retrieval_context_sources": context_sources}
                            if context else {})
        # ID에는 제목·추가 문맥이 포함되지 않는다. embedding_text가 바뀌면 같은 ID라도 재임베딩한다.
        key = f"v2:{unit['unit_id']}:{start}:{end}:{size}:{overlap}:{text}"
        metadata = {k: v for k, v in unit.items() if k not in (
            "text", "tables", "table", "evidence_metadata", "document_constraints")}
        yield {**metadata, **context_metadata, "schema_version": 2,
               "chunk_id": hashlib.sha256(key.encode()).hexdigest()[:24],
               "parent_id": unit["unit_id"], "start": start, "end": end,
               "offset_basis": "parent.text", "text": text,
               "embedding_text": header + "\n" + (context + "\n" if context else "") + text,
               "requires_parent_evidence": True,
               "retrieval_only": True,
               "split_basis": ("legal_structure_with_parent_evidence" if structured
                               else "boundary_window_with_parent_evidence")}


def stats(rows):
    """청크 본문과 검색 입력의 문자 수를 집계한다. 토큰 수 통계는 아니다."""
    lengths = sorted(len(r["text"]) for r in rows)
    embedding_lengths = [len(row.get("embedding_text", row["text"])) for row in rows]
    return {"count": len(rows), "min_chars": min(lengths, default=0),
            "median_chars": lengths[len(lengths) // 2] if lengths else 0,
            "p95_chars": lengths[int(len(lengths) * .95)] if lengths else 0,
            "max_chars": max(lengths, default=0), "under_100_chars": sum(n < 100 for n in lengths),
            "over_2000_chars": sum(n > 2000 for n in lengths),
            "max_embedding_chars": max(embedding_lengths, default=0)}


def validate(units, chunks, size):
    """ID 중복, 부모 본문 위치, 추가 문맥 출처, 길이와 본문 누락을 검사한다."""
    parents = {unit["unit_id"]: unit for unit in units}
    seen, covered = set(), {}
    for chunk in chunks:
        if chunk["chunk_id"] in seen:
            raise ValueError("Duplicate chunk ID")
        seen.add(chunk["chunk_id"])
        parent = parents[chunk["parent_id"]]
        if chunk["text"] != parent["text"][chunk["start"]:chunk["end"]]:
            raise ValueError("Source offset mismatch")
        context = chunk.get("retrieval_context", "")
        expected_context, expected_sources = retrieval_context(parent, chunk["start"], chunk["end"], size)
        if context != expected_context or chunk.get("retrieval_context_sources", []) != expected_sources:
            raise ValueError("Retrieval context does not match parent evidence")
        context_chars = len(context)
        context_overhead = context_chars + SEPARATOR_CHARS if context else 0
        embedding_limit = size + MAX_HEADER_CHARS + SEPARATOR_CHARS + context_overhead
        exceeds_context_limit = context_chars > MAX_CONTEXT_CHARS
        exceeds_body_limit = len(chunk["text"]) > size
        exceeds_embedding_limit = len(chunk["embedding_text"]) > embedding_limit
        if exceeds_context_limit or exceeds_body_limit or exceeds_embedding_limit:
            raise ValueError("Chunk size limit exceeded")
        covered.setdefault(chunk["parent_id"], []).append((chunk["start"], chunk["end"]))
    for key, parent in parents.items():
        cursor = 0
        # 겹치는 청크는 허용하되, 청크 사이에 빠진 본문이 있는지 확인한다.
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

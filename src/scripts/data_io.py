"""자료 종류별 데이터 읽기·저장과 원본 해시 검증."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("consumer_sources", "guides", "laws")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_chunks(chunk_dir=None):
    """자료 종류별 JSONL에서 검색 청크를 읽는다."""
    chunk_dir = Path(chunk_dir or ROOT / "data/chunk")
    for path in sorted(chunk_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                yield json.loads(line)


def validate_source_hashes(units, raw_dir):
    """원본별 해시를 한 번씩 검사한다."""
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
    """청킹할 근거를 읽고 원본 변경 여부를 검증한다."""
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


def kind_for(source_path):
    category = PurePosixPath(source_path).parts[2]
    return category if category in KINDS else "laws"


def processed_path(source_path, document_id):
    source = PurePosixPath(source_path)
    kind = kind_for(source_path)
    subdir = {"law_appendices": "appendices", "law_history": "history"}.get(source.parts[2])
    path = PurePosixPath("processed") / kind
    if subdir:
        path /= subdir
    return path / (document_id + ".json")


def save_processed_data(processed_dir, documents, pdf_pages=None):
    """원본 문서별 전처리 결과를 저장한다."""
    processed_dir = Path(processed_dir)
    pdf_pages = pdf_pages or {}
    saved = []
    grouped = defaultdict(list)
    for unit in documents:
        grouped[unit["source_path"]].append(unit)
    for source, units in grouped.items():
        first = units[0]
        relative = processed_path(source, first["document_id"]).relative_to("processed")
        if ".." in relative.parts:
            raise ValueError("Unsafe processed document path")
        destination = processed_dir / str(relative)
        if not destination.resolve().is_relative_to(processed_dir.resolve()):
            raise ValueError("Processed path escapes its output directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = {"schema_version": 3, "document_id": first["document_id"],
                    "title": first["title"], "source_path": source,
                    "raw_sha256": first["raw_sha256"], "units": units}
        if source in pdf_pages:
            document["pages"] = pdf_pages[source]
        destination.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        saved.append(destination)
    return saved


def save_chunks(chunk_dir, chunks):
    """청크를 자료 종류별 JSONL에 저장한다."""
    chunk_dir = Path(chunk_dir)
    grouped = {kind: [] for kind in KINDS}
    for chunk in chunks:
        grouped[kind_for(chunk["source_path"])].append(chunk)
    saved = []
    for kind in KINDS:
        destination = chunk_dir / kind / "chunks.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n"
                                      for c in grouped[kind]), encoding="utf-8")
        saved.append(destination)
    return saved


def iter_processed_units(processed_dir):
    """전처리 문서에서 검색 대상 근거만 읽는다."""
    for path in sorted(Path(processed_dir).rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(doc, dict) and doc.get("schema_version") == 3:
            yield from (unit for unit in doc["units"] if unit.get("embedding_candidate"))

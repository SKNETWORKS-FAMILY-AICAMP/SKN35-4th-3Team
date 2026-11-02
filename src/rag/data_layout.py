"""Keep raw, processed and chunk outputs grouped by their original source type."""

from collections import defaultdict
import json
from pathlib import Path, PurePosixPath


KINDS = ("consumer_sources", "guides", "laws")


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
    """Save document-level extraction results; do not write chunk files."""
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
    """Save chunks by source kind; leave processed documents unchanged."""
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


def write_dataset(output, documents, chunks, pdf_pages=None):
    """Compatibility wrapper for callers that deliberately save both stages."""
    output = Path(output)
    return {"processed": save_processed_data(output / "processed", documents, pdf_pages),
            "chunk": save_chunks(output / "chunk", chunks)}


def read_processed_units(data_dir):
    yield from iter_processed_units(Path(data_dir) / "processed")


def iter_processed_units(processed_dir):
    """Yield eligible evidence from a supplied processed directory."""
    for path in sorted(Path(processed_dir).rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(doc, dict) and doc.get("schema_version") == 3:
            yield from (unit for unit in doc["units"] if unit.get("embedding_candidate"))

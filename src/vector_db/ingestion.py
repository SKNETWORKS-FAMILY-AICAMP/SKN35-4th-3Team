"""4차 청크 적재와 3차 판례 Point 복사를 위한 Qdrant 작업."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import uuid
from pathlib import Path

from dotenv import dotenv_values
from fastembed import SparseTextEmbedding
from openai import OpenAI
from qdrant_client import QdrantClient, models

from src.const.config import CHUNK_DATA_PATH, ENV_PATH
from src.scripts.data_io import load_chunks, load_processed_data

SOURCE_COLLECTION = "team_documents_openai_dev"
TARGET_COLLECTION = "SKN35-4th-3Team"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
SPARSE_MODEL = "Qdrant/bm25"
SPARSE_VECTOR_NAME = "bm25"
EXPECTED_CHUNKS = 4_276
EXPECTED_PRECEDENT_POINTS = 359
INDEX_FIELDS = (
    "document_id",
    "document_type",
    "source_name",
    "parent_id",
    "precedent_id",
    "case_number",
    "case_name",
    "source_project",
)


def _required(values: dict, name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}을(를) 환경 파일에 입력해야 합니다.")
    return value.strip()


def _point_id(kind: str, *parts: str) -> str:
    identity = json.dumps((TARGET_COLLECTION, kind, *parts), ensure_ascii=False)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunk_payload(row: dict) -> dict:
    payload = dict(row)
    payload.update(
        source_name=row["title"],
        source_project="4th",
        embedding_provider="openai",
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
        content_hash=_hash_text(row["text"]),
        embedding_input_hash=_hash_text(
            f"{EMBEDDING_MODEL}\0{SPARSE_MODEL}\0{row['embedding_text']}"
        ),
    )
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload["payload_hash"] = _hash_text(serialized)
    return payload


def load_chunk_rows(expected_count: int = EXPECTED_CHUNKS) -> list[dict]:
    """저장된 청크와 전처리 근거의 식별자·본문 위치를 확인합니다."""

    for folder in ("consumer_sources", "guides", "laws"):
        path = CHUNK_DATA_PATH / folder / "chunks.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"청크 파일이 없습니다: {path}")

    rows = list(load_chunks(CHUNK_DATA_PATH))
    if len(rows) != expected_count:
        raise ValueError(
            f"청크 수가 예상과 다릅니다: expected={expected_count}, actual={len(rows)}"
        )
    identities: set[tuple[str, str]] = set()
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise TypeError(f"{number}번째 청크가 객체가 아닙니다.")
        for key in (
            "document_id",
            "chunk_id",
            "text",
            "embedding_text",
            "parent_id",
            "title",
            "document_type",
        ):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"{number}번째 청크에 {key}가 없습니다.")
        identity = (row["document_id"], row["chunk_id"])
        if identity in identities:
            raise ValueError(f"중복 청크 식별자: {identity}")
        identities.add(identity)
    parents = {
        unit["unit_id"]: unit
        for unit in load_processed_data(verify_sources=False)
    }
    for row in rows:
        parent = parents.get(row["parent_id"])
        if parent is None or row["text"] != parent["text"][row["start"] : row["end"]]:
            raise ValueError(f"부모 근거와 청크 본문이 다릅니다: {row['chunk_id']}")
    return rows


def _precedent_filter() -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="document_type",
                match=models.MatchValue(value="precedent"),
            )
        ]
    )


def _scroll(
    client: QdrantClient,
    collection: str,
    *,
    query_filter: models.Filter | None = None,
    with_vectors: bool = False,
):
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=query_filter,
            offset=offset,
            limit=100,
            with_payload=True,
            with_vectors=with_vectors,
        )
        yield from points
        if offset is None:
            break


def validate_target_schema(client: QdrantClient) -> None:
    """기존 컬렉션을 교체하거나 재생성하지 않고 벡터 규격만 확인합니다."""

    info = client.get_collection(TARGET_COLLECTION)
    params = info.config.params
    vectors = params.vectors
    if (
        not isinstance(vectors, models.VectorParams)
        or vectors.size != EMBEDDING_DIMENSION
        or vectors.distance != models.Distance.COSINE
    ):
        raise ValueError("4차 컬렉션의 Dense 벡터 규격이 다릅니다.")
    sparse = params.sparse_vectors or {}
    if SPARSE_VECTOR_NAME not in sparse:
        raise ValueError("4차 컬렉션에 bm25 Sparse 벡터가 없습니다.")


def ensure_payload_indexes(client: QdrantClient) -> list[str]:
    """실제로 필터·정확 조회에 사용하는 Keyword 인덱스만 만듭니다."""

    validate_target_schema(client)
    existing = client.get_collection(TARGET_COLLECTION).payload_schema or {}
    created: list[str] = []
    for field in INDEX_FIELDS:
        if field in existing:
            continue
        client.create_payload_index(
            collection_name=TARGET_COLLECTION,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
        created.append(field)
    return created


def copy_precedents(
    client: QdrantClient,
    *,
    expected_count: int = EXPECTED_PRECEDENT_POINTS,
) -> int:
    """3차 컬렉션을 읽기만 하고 판례 벡터·Payload를 4차에 복사합니다."""

    validate_target_schema(client)
    source_count = client.count(
        collection_name=SOURCE_COLLECTION,
        count_filter=_precedent_filter(),
        exact=True,
    ).count
    if source_count != expected_count:
        raise ValueError(
            f"3차 판례 Point 수가 예상과 다릅니다: "
            f"expected={expected_count}, actual={source_count}"
        )

    copied = 0
    batch: list[models.PointStruct] = []
    for point in _scroll(
        client,
        SOURCE_COLLECTION,
        query_filter=_precedent_filter(),
        with_vectors=True,
    ):
        if not isinstance(point.vector, dict) or not {
            "",
            SPARSE_VECTOR_NAME,
        }.issubset(point.vector):
            raise ValueError(f"3차 판례 Point의 벡터가 불완전합니다: {point.id}")
        if len(point.vector[""]) != EMBEDDING_DIMENSION:
            raise ValueError(f"3차 판례 Point의 차원이 다릅니다: {point.id}")
        payload = dict(point.payload or {})
        if payload.get("document_type") != "precedent":
            raise ValueError(f"판례가 아닌 Point가 섞였습니다: {point.id}")
        payload.update(
            source_project="3rd",
            copied_from_collection=SOURCE_COLLECTION,
            source_point_id=str(point.id),
        )
        batch.append(
            models.PointStruct(
                id=_point_id("precedent", str(point.id)),
                vector=point.vector,
                payload=payload,
            )
        )
        if len(batch) == 100:
            client.upsert(collection_name=TARGET_COLLECTION, points=batch, wait=True)
            copied += len(batch)
            print(f"판례 복사: {copied}/{expected_count}", flush=True)
            batch.clear()
    if batch:
        client.upsert(collection_name=TARGET_COLLECTION, points=batch, wait=True)
        copied += len(batch)
        print(f"판례 복사: {copied}/{expected_count}", flush=True)
    if copied != expected_count:
        raise RuntimeError(f"복사한 판례 수가 다릅니다: {copied}/{expected_count}")
    return copied


def ingest_chunks(
    client: QdrantClient,
    openai_key: str,
    *,
    expected_count: int = EXPECTED_CHUNKS,
    batch_size: int = 64,
) -> dict[str, int]:
    """변경된 embedding_text만 새로 임베딩해 배치 Upsert합니다."""

    validate_target_schema(client)
    if not 1 <= batch_size <= 128:
        raise ValueError("batch_size는 1~128이어야 합니다.")
    rows = load_chunk_rows(expected_count)
    ai = OpenAI(api_key=openai_key, max_retries=5, timeout=90)
    sparse_model = SparseTextEmbedding(
        model_name=SPARSE_MODEL, disable_stemmer=True
    )
    counts = {"embedded": 0, "payload_only": 0, "unchanged": 0, "tokens": 0}
    try:
        for start in range(0, len(rows), batch_size):
            batch_rows = rows[start : start + batch_size]
            items = [
                (
                    row,
                    _point_id("chunk", row["document_id"], row["chunk_id"]),
                    _chunk_payload(row),
                )
                for row in batch_rows
            ]
            existing = {
                str(point.id): point
                for point in client.retrieve(
                    collection_name=TARGET_COLLECTION,
                    ids=[point_id for _, point_id, _ in items],
                    with_payload=True,
                    with_vectors=False,
                )
            }
            to_embed = []
            payload_only = []
            for row, point_id, payload in items:
                stored = existing.get(point_id)
                stored_payload = (stored.payload or {}) if stored else {}
                if stored and stored_payload.get("source_project") != "4th":
                    raise ValueError(f"다른 자료의 Point ID와 충돌합니다: {point_id}")
                if (
                    stored
                    and stored_payload.get("embedding_input_hash")
                    == payload["embedding_input_hash"]
                ):
                    if stored_payload.get("payload_hash") == payload["payload_hash"]:
                        counts["unchanged"] += 1
                    else:
                        payload_only.append((point_id, payload))
                else:
                    to_embed.append((row, point_id, payload))

            upserts: list[models.PointStruct] = []
            if payload_only:
                reusable = {
                    str(point.id): point
                    for point in client.retrieve(
                        collection_name=TARGET_COLLECTION,
                        ids=[point_id for point_id, _ in payload_only],
                        with_payload=False,
                        with_vectors=True,
                    )
                }
                for point_id, payload in payload_only:
                    stored = reusable.get(point_id)
                    if stored is None or not isinstance(stored.vector, dict):
                        raise ValueError(f"기존 벡터를 읽을 수 없습니다: {point_id}")
                    upserts.append(
                        models.PointStruct(
                            id=point_id, vector=stored.vector, payload=payload
                        )
                    )
                counts["payload_only"] += len(payload_only)

            if to_embed:
                texts = [row["embedding_text"] for row, _, _ in to_embed]
                response = ai.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=texts,
                    dimensions=EMBEDDING_DIMENSION,
                    encoding_format="float",
                )
                dense_vectors = [
                    item.embedding
                    for item in sorted(response.data, key=lambda item: item.index)
                ]
                sparse_vectors = list(
                    sparse_model.passage_embed(texts, batch_size=len(texts))
                )
                if len(dense_vectors) != len(texts) or len(sparse_vectors) != len(texts):
                    raise RuntimeError("임베딩 응답 수가 입력 청크 수와 다릅니다.")
                for (_, point_id, payload), dense, sparse in zip(
                    to_embed, dense_vectors, sparse_vectors, strict=True
                ):
                    if len(dense) != EMBEDDING_DIMENSION or not all(
                        math.isfinite(value) for value in dense
                    ):
                        raise ValueError(f"Dense 벡터 차원·값 오류: {point_id}")
                    upserts.append(
                        models.PointStruct(
                            id=point_id,
                            vector={
                                "": dense,
                                SPARSE_VECTOR_NAME: models.SparseVector(
                                    indices=[int(value) for value in sparse.indices],
                                    values=[float(value) for value in sparse.values],
                                ),
                            },
                            payload=payload,
                        )
                    )
                counts["embedded"] += len(to_embed)
                counts["tokens"] += response.usage.total_tokens if response.usage else 0

            if upserts:
                client.upsert(
                    collection_name=TARGET_COLLECTION, points=upserts, wait=True
                )
            print(
                f"청크 적재: {min(start + batch_size, len(rows))}/{len(rows)} "
                f"(신규·변경 {counts['embedded']}, Payload만 {counts['payload_only']}, "
                f"건너뜀 {counts['unchanged']})",
                flush=True,
            )
    finally:
        ai.close()
    return counts


def verify_ingestion(
    client: QdrantClient,
    *,
    expected_chunks: int = EXPECTED_CHUNKS,
    expected_precedents: int = EXPECTED_PRECEDENT_POINTS,
) -> dict[str, int]:
    """자동 삭제 없이 누락·잔여 Point를 식별합니다."""

    rows = load_chunk_rows(expected_chunks)
    chunk_ids = {
        _point_id("chunk", row["document_id"], row["chunk_id"]) for row in rows
    }
    precedent_ids = {
        _point_id("precedent", str(point.id))
        for point in _scroll(
            client, SOURCE_COLLECTION, query_filter=_precedent_filter()
        )
    }
    if len(precedent_ids) != expected_precedents:
        raise ValueError("3차 판례 수가 예상과 달라 검증할 수 없습니다.")
    expected = chunk_ids | precedent_ids
    actual: set[str] = set()
    wrong_payload = 0
    for point in _scroll(client, TARGET_COLLECTION):
        point_id = str(point.id)
        actual.add(point_id)
        payload = point.payload or {}
        if point_id in chunk_ids and payload.get("source_project") != "4th":
            wrong_payload += 1
        if point_id in precedent_ids and (
            payload.get("source_project") != "3rd"
            or payload.get("document_type") != "precedent"
        ):
            wrong_payload += 1
    report = {
        "expected_chunks": len(chunk_ids),
        "expected_precedents": len(precedent_ids),
        "actual_points": len(actual),
        "missing": len(expected - actual),
        "residual": len(actual - expected),
        "wrong_payload": wrong_payload,
    }
    if report["missing"] or report["residual"] or wrong_payload:
        raise RuntimeError(f"Qdrant 적재 검증 실패: {report}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="4차 Qdrant 컬렉션 적재")
    parser.add_argument(
        "action", choices=("setup", "copy-precedents", "ingest-chunks", "verify")
    )
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--expected-chunks", type=int, default=EXPECTED_CHUNKS)
    parser.add_argument(
        "--expected-precedents", type=int, default=EXPECTED_PRECEDENT_POINTS
    )
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    client = QdrantClient(
        url=_required(values, "QDRANT_URL"),
        api_key=_required(values, "QDRANT_API_KEY"),
        timeout=60,
    )
    try:
        if args.action == "setup":
            result = {"created_indexes": ensure_payload_indexes(client)}
        elif args.action == "copy-precedents":
            result = {
                "copied_precedents": copy_precedents(
                    client, expected_count=args.expected_precedents
                )
            }
        elif args.action == "ingest-chunks":
            result = ingest_chunks(
                client,
                _required(values, "OPENAI_API_KEY"),
                expected_count=args.expected_chunks,
                batch_size=args.batch_size,
            )
        else:
            result = verify_ingestion(
                client,
                expected_chunks=args.expected_chunks,
                expected_precedents=args.expected_precedents,
            )
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()

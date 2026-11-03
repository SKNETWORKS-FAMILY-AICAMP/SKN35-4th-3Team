"""Fuse ranked search results using reciprocal rank fusion."""

import math
from collections.abc import Mapping, Sequence
from typing import Any


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[dict[str, Any]]] | Sequence[Sequence[dict[str, Any]]],
    *,
    top_k: int = 20,
    rank_constant: float = 60.0,
    dedup_by: str = "chunk_id",
) -> list[dict[str, Any]]:
    """Combine rankings, counting each document once per source."""
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("top_k must be an integer between 1 and 100")
    if not math.isfinite(rank_constant) or rank_constant <= 0:
        raise ValueError("rank_constant must be finite and positive")
    if dedup_by not in {"chunk_id", "point_id"}:
        raise ValueError("dedup_by must be chunk_id or point_id")

    if isinstance(rankings, Mapping):
        named_rankings = rankings.items()
    elif isinstance(rankings, Sequence) and not isinstance(rankings, (str, bytes)):
        named_rankings = ((f"ranking_{i}", results) for i, results in enumerate(rankings, 1))
    else:
        raise TypeError("rankings는 검색 결과 목록들의 dict 또는 list여야 합니다.")

    combined: dict[tuple[str, ...], dict[str, Any]] = {}

    for source_name, results in named_rankings:
        if not isinstance(source_name, str) or not source_name:
            raise ValueError("검색 목록 이름은 비어 있지 않은 문자열이어야 합니다.")
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            raise TypeError(f"{source_name}의 결과는 list 형태여야 합니다.")

        seen: set[tuple[str, ...]] = set()
        for rank, raw in enumerate(results, start=1):
            if not isinstance(raw, dict):
                raise TypeError(f"{source_name}의 {rank}위 결과가 dict가 아닙니다.")

            payload = raw.get("payload") or raw.get("metadata") or {}
            if not isinstance(payload, dict):
                payload = {}
            point_id = str(raw.get("point_id") or raw.get("id") or "").strip()
            document_id = payload.get("document_id")
            chunk_id = payload.get("chunk_id")
            has_chunk_identity = all(
                isinstance(value, str) and bool(value.strip())
                for value in (document_id, chunk_id)
            )

            if dedup_by == "chunk_id" and has_chunk_identity:
                key = ("chunk", document_id, chunk_id)
            elif point_id:
                key = ("point", point_id)
            elif has_chunk_identity:
                key = ("chunk", document_id, chunk_id)
            else:
                # 식별할 수 없는 문서는 안전하게 제외
                continue

            if key in seen:
                continue
            seen.add(key)

            text = raw.get("text") or payload.get("text") or payload.get("embedding_text") or ""
            raw_score = raw.get("score")
            try:
                original_score = float(raw_score) if raw_score is not None else None
                if original_score is not None and not math.isfinite(original_score):
                    original_score = None
            except (TypeError, ValueError):
                original_score = None

            if key not in combined:
                combined[key] = {
                    "point_id": point_id,
                    "text": str(text),
                    "payload": dict(payload),
                    "rrf_score": 0.0,
                    "rrf_sources": {},
                }
            item = combined[key]
            if not item["text"] and text:
                item["text"] = str(text)
            if not item["point_id"] and point_id:
                item["point_id"] = point_id
            if not item["payload"] and payload:
                item["payload"] = dict(payload)

            item["rrf_score"] += 1.0 / (rank_constant + rank)
            item["rrf_sources"][source_name] = {
                "rank": rank,
                "score": original_score,
            }

    ordered = sorted(
        combined.values(),
        key=lambda doc: (-doc["rrf_score"], doc["point_id"], str(doc["payload"].get("chunk_id", ""))),
    )[:top_k]

    return [
        {
            **doc,
            "score": doc["rrf_score"],  # 후속 단계의 공통 score 필드
            "metadata": {
                **doc["payload"],
                "retrieval_method": "rrf",
                "rrf_sources": doc["rrf_sources"],
            },
        }
        for doc in ordered
    ]


def rrf(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """LangGraph 노드. state['rankings']를 받아 state['documents']로 반환."""
    return {
        "documents": reciprocal_rank_fusion(
            state.get("rankings") or {},
            top_k=20,
        )
    }


if __name__ == "__main__":
    from src.rag.hybrid_search import hybrid_search

    question = input("검색할 질문을 입력하세요: ").strip()
    if not question:
        raise ValueError("검색 질문을 입력해야 합니다.")

    results = hybrid_search(question, top_k=20)
    rankings = {
        "q1_dense": results["dense_results"],
        "q1_bm25": results["bm25_results"],
    }
    fused = reciprocal_rank_fusion(rankings, top_k=20)
    print(f"Dense: {len(rankings['q1_dense'])}개 | BM25: {len(rankings['q1_bm25'])}개 | RRF: {len(fused)}개")
    for index, doc in enumerate(fused[:5], 1):
        print(f"\n{index}위 | RRF Score: {doc['rrf_score']:.6f}")
        print("Point ID:", doc["point_id"])
        print("Chunk ID:", doc["payload"].get("chunk_id"))
        print("문서:", doc["payload"].get("source_name"))
        print("검색 출처:", doc["rrf_sources"])
        print("내용:", doc["text"][:200])

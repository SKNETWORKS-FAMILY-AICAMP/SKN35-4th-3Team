"""Dense·BM25·Multi-Query 결과를 순위 기반 RRF로 통합합니다."""

import math


def _document(result: dict) -> dict:
    payload = result.get("payload") or result.get("metadata") or {}
    if not isinstance(payload, dict):
        payload = {}
    text = result.get("text") or payload.get("text") or payload.get("embedding_text")
    return {
        "point_id": str(result.get("point_id") or result.get("id") or ""),
        "text": str(text or "").strip(),
        "score": float(result.get("score") or 0.0),
        "metadata": dict(payload),
    }


def reciprocal_rank_fusion(
    rankings: dict[str, list[dict]],
    *,
    top_k: int = 20,
    rank_constant: float = 60.0,
) -> list[dict]:
    if not 1 <= top_k <= 100:
        raise ValueError("top_k는 1~100이어야 합니다.")
    if not math.isfinite(rank_constant) or rank_constant <= 0:
        raise ValueError("rank_constant는 유한한 양수여야 합니다.")

    scores: dict[tuple, float] = {}
    documents: dict[tuple, dict] = {}
    sources: dict[tuple, dict] = {}
    for name, results in rankings.items():
        seen: set[tuple] = set()
        for rank, raw in enumerate(results, start=1):
            document = _document(raw)
            if not document["text"]:
                continue
            metadata = document["metadata"]
            identity = (metadata.get("document_id"), metadata.get("chunk_id"))
            key = (
                ("chunk", *identity)
                if all(isinstance(value, str) and value for value in identity)
                else ("point", document["point_id"])
            )
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1 / (rank_constant + rank)
            documents.setdefault(key, document)
            sources.setdefault(key, {})[name] = {
                "rank": rank,
                "score": document["score"],
            }

    ordered = sorted(scores, key=lambda key: (-scores[key], str(key)))[:top_k]
    response: list[dict] = []
    for key in ordered:
        document = documents[key]
        response.append(
            {
                **document,
                "score": scores[key],
                "metadata": {
                    **document["metadata"],
                    "retrieval_method": "rrf",
                    "rrf_sources": sources[key],
                },
            }
        )
    return response


def rrf(state: dict) -> dict[str, list[dict]]:
    return {
        "documents": reciprocal_rank_fusion(
            state.get("rankings") or {},
            top_k=20,
        )
    }

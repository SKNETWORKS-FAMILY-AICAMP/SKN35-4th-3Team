"""RRF 후보를 원 질문 기준으로 재정렬합니다."""

import math
import os
from time import perf_counter

from src.const.model import create_rerank_model

MIN_RELEVANCE_SCORE = float(os.getenv("RERANK_MIN_SCORE", "0.1"))


def _retrieval_score(document: dict) -> float:
    score = document.get("score", 0.0)
    return float(score) if isinstance(score, (int, float)) else 0.0


def rerank(
    state: dict,
    *,
    top_k: int = 5,
    timings: dict | None = None,
) -> dict[str, object]:
    started = perf_counter()
    documents = list(state.get("documents") or [])
    question = state.get("rewrite_query") or state.get("origin_query") or ""
    if not documents:
        return {"documents": [], "score": 0.0}

    mode = "retrieval_score"
    try:
        model = create_rerank_model()
        pairs = [(question, document.get("text", "")) for document in documents]
        predictions = model.predict(pairs, batch_size=8, show_progress_bar=False)
        scores = [float(value) for value in predictions]
        mode = "cross_encoder"
    except (ModuleNotFoundError, RuntimeError, ValueError):
        scores = [_retrieval_score(document) for document in documents]

    ranked: list[dict] = []
    for document, score in zip(documents, scores):
        if not math.isfinite(score):
            continue
        ranked.append(
            {
                **document,
                "score": score,
                "metadata": {
                    **(document.get("metadata") or {}),
                    "rerank_mode": mode,
                },
            }
        )
    ranked.sort(key=lambda document: -document["score"])
    selected = ranked[:top_k]
    best_score = selected[0]["score"] if selected else 0.0
    if timings is not None:
        timings.update(
            best_score=best_score,
            min_score=selected[-1]["score"] if selected else None,
            filtered_out_count=max(0, len(ranked) - len(selected)),
            latency_ms=round((perf_counter() - started) * 1000, 1),
        )
    return {"documents": selected, "score": best_score}

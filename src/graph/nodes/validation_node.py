from src.graph.state import State
from src.rag.rerank import MIN_RELEVANCE_SCORE
import math

MAX_RETRY = 2


def validation_node(state: State):
    documents = state.get("documents") or []
    score = state.get("score")

    has_score = score is not None and math.isfinite(float(score))
    has_evidence = bool(documents) and has_score and float(score) >= MIN_RELEVANCE_SCORE
    if has_evidence:
        return {"is_valid": True, "validation_reason": "usable_evidence_found"}

    # 실패 원인 구분: 근거 자체가 없는 경우와, 있지만 점수가 기준 미달인 경우
    reason = "empty_evidence" if not documents or not has_score else "low_relevance_score"

    exhausted = state.get("retry_count", 0) >= MAX_RETRY
    return {
        "is_valid": exhausted,
        "validation_reason": f"{reason}_retry_exhausted" if exhausted else f"{reason}_retry",
    }
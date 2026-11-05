"""판례 조회 요청에는 검색된 판례 정보와 원문 발췌를 직접 반환합니다."""

import re

from src.rag.generator import build_context


def precedent_results_node(state):
    prepared = build_context(state.get("documents") or [])
    if not prepared["sources"]:
        failed = state.get("mcp_status") not in {"success", "empty_result"}
        return {
            "answer": "판례 검색 중 오류가 발생했습니다." if failed else "이번 검색에서 관련 판례를 찾지 못했습니다. 사건의 쟁점이나 상황을 더 구체적으로 알려주세요.",
            "generation_status": "retrieval_error" if failed else "insufficient_evidence",
            "sources": [], "context_sources": [], "citations": [],
        }
    lines = ["검색된 판례입니다. 사건의 사실관계와 적용 가능성은 원문에서 확인하세요."]
    for index, document in enumerate(prepared["documents"], 1):
        identity = " · ".join(str(document[key]) for key in ("case_name", "case_number", "court", "decision_date") if document.get(key))
        text = re.sub(r"<[^>]+>", " ", document["text"])
        excerpt = " ".join(text.split())[:250]
        lines.append(f"\n{index}. {identity or document.get('source_name') or '판례'}\n본문 발췌: {excerpt}…")
    return {
        "answer": "\n".join(lines), "generation_status": "answered",
        "sources": prepared["sources"], "citations": prepared["sources"],
        "context_sources": prepared["sources"],
        "citation_validation_status": "retrieved_original",
    }

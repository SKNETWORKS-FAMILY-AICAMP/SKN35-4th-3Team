"""Generator의 주장별 출처 ID를 실제 검색 출처와 대조합니다."""

from time import perf_counter


def citation(state: dict) -> dict[str, object]:
    started = perf_counter()
    status = state.get("generation_status")
    if status != "generated_unverified":
        return {
            "answer": state.get("answer", ""),
            "citations": [],
            "sources": [],
            "citation_validation_status": "skipped",
            "validation_errors": [],
            "citation_latency_ms": round((perf_counter() - started) * 1000, 1),
        }

    source_list = state.get("context_sources") or []
    source_map = {
        source.get("source_id"): source
        for source in source_list
        if isinstance(source, dict) and source.get("source_id")
    }
    claims = state.get("claims") or []
    errors: list[dict[str, object]] = []
    used_ids: list[str] = []
    if not claims:
        errors.append({"code": "missing_claim_mapping"})
    for claim in claims:
        ids = claim.get("source_ids") if isinstance(claim, dict) else None
        if not isinstance(ids, list) or not ids:
            errors.append({"code": "claim_without_citation"})
            continue
        for source_id in ids:
            if source_id not in source_map:
                errors.append(
                    {"code": "unknown_citation", "source_id": source_id}
                )
            elif source_id not in used_ids:
                used_ids.append(source_id)

    citations = [source_map[source_id] for source_id in used_ids]
    passed = not errors
    return {
        "answer": (
            state.get("answer", "")
            if passed
            else "인용 출처를 확인할 수 없어 답변을 보류합니다."
        ),
        "citations": citations if passed else [],
        "sources": citations if passed else [],
        "used_document_ids": list(
            dict.fromkeys(
                source.get("document_id") or source.get("point_id")
                for source in citations
                if source.get("document_id") or source.get("point_id")
            )
        ),
        "citation_validation_status": "passed" if passed else "failed",
        "validation_errors": errors,
        "citation_latency_ms": round((perf_counter() - started) * 1000, 1),
    }

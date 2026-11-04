
"""Generator 답변의 출처와 법적 근거를 검증합니다."""

import json
import os
from time import perf_counter

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.const.prompt.citation_prompt import CITATION_PROMPT

load_dotenv()


def citation(state: dict, *, llm=None) -> dict:
    """Generator 결과를 검증하고 인용 출처를 연결합니다."""

    started = perf_counter()

    if state.get("generation_status") != "generated_unverified":
        return {
            "citation_validation_status": "skipped",
            "citation_latency_ms": round((perf_counter() - started) * 1000, 1),
        }

    sources = state.get("context_sources") or []
    documents = state.get("context_documents") or []
    claims = state.get("claims") or []

    source_map = {
        source["source_id"]: source
        for source in sources
        if isinstance(source, dict) and source.get("source_id")
    }

    # 기존 코드의 출처 ID 검증
    errors = []
    for claim in claims:
        ids = claim.get("source_ids") if isinstance(claim, dict) else None
        if not isinstance(ids, list) or not ids:
            errors.append({"code": "claim_without_citation"})
        elif any(sid not in source_map for sid in ids):
            errors.append({"code": "unknown_citation"})

    if not claims:
        errors.append({"code": "missing_claim_mapping"})

    if not documents or not source_map:
        errors.append({"code": "missing_context"})

    result = {
        "generation_status": "verification_failed",
        "verification_passed": False,
        "citation_validation_status": "failed",
        "citations": [],
        "sources": [],
        "used_document_ids": [],
        "citation_evaluations": [],
        "validation_errors": errors,
        "review_notes": [],
    }

    if not errors:
        try:
            model = llm if llm is not None else ChatOpenAI(
                model=os.environ["OPENAI_MODEL"],
                temperature=0,
                model_kwargs={
                    "response_format": {"type": "json_object"}
                },
            )

            response = model.invoke([
                SystemMessage(content=CITATION_PROMPT),
                HumanMessage(content=json.dumps({
                    "question": state.get("origin_query", ""),
                    "answer": state.get("answer", ""),
                    "claims": claims,
                    "documents": documents,
                }, ensure_ascii=False, default=str)),
            ])

            output = json.loads(response.content)
            evaluations = output.get("evaluations", [])

            if not isinstance(evaluations, list):
                raise ValueError("evaluations는 목록이어야 합니다.")

            used_ids = list(dict.fromkeys(
                sid
                for item in evaluations
                if isinstance(item, dict)
                and item.get("verdict") in ("supported", "partial")
                for sid in item.get("source_ids", [])
                if sid in source_map
            ))

            citations = [source_map[sid] for sid in used_ids]
            passed = output.get("passed") is True and bool(citations)

            result.update(
                generation_status=(
                    "answered" if passed else "verification_failed"
                ),
                verification_passed=passed,
                citation_validation_status=(
                    "passed" if passed else "failed"
                ),
                citations=citations,
                sources=citations,
                used_document_ids=list(dict.fromkeys(
                    source.get("document_id") or source.get("point_id")
                    for source in citations
                    if source.get("document_id") or source.get("point_id")
                )),
                citation_evaluations=evaluations,
                review_notes=output.get("review_notes", []),
            )

        except Exception as error:
            result.update(
                generation_status="generated_unverified",
                citation_validation_status="error",
                validation_errors=[{"code": "citation_error"}],
                review_notes=["근거 검증을 완료하지 못했습니다."],
                citation_error=str(error),
            )

    result["citation_latency_ms"] = round(
        (perf_counter() - started) * 1000, 1
    )

    return result


# =================================
# test


if __name__ == "__main__":
    from src.rag.query_planner import query_planner
    from src.rag.query_rewrite import query_rewrite
    from src.rag.multi_query import multi_query
    from src.rag.hybrid_search import hybrid_search
    from src.rag.rrf import rrf
    from src.rag.rerank import rerank
    from src.rag.generator import generator

    question = input("질문을 입력하세요: ").strip()

    # 1. Query Planner
    print("\n[1. Query Planner]")
    plan = query_planner(question)
    print(plan)

    # 2. Query Rewrite
    print("\n[2. Query Rewrite]")
    rewritten = query_rewrite(question)
    print(rewritten)

    # 3. Multi-Query
    print("\n[3. Multi-Query]")
    expanded = multi_query(rewritten)
    print(expanded)

    # 4. Hybrid Search
    print("\n[4. Hybrid Search]")
    queries = [question, rewritten, *expanded]
    search_results = [
        hybrid_search(query, top_k=40)
        for query in dict.fromkeys(queries)
    ]

    # 5. RRF
    print("\n[5. RRF]")
    fused = rrf(search_results, top_k=40)

    # 6. Rerank
    print("\n[6. Rerank]")
    documents = rerank(question, fused, top_k=5)

    # 7. Generator
    print("\n[7. Generator]")
    state = generator({
        "origin_query": question,
        "documents": documents,
    })

    print("답변:", state["answer"])
    print("상태:", state["generation_status"])

    # 8. Citation
    print("\n[8. Citation]")
    result = citation({
        **state,
        "origin_query": question,
    })

    state.update(result)

    print("\n[최종 답변]")
    print(state["answer"])

    print("\n[최종 상태]")
    print(state["generation_status"])

    print("\n[검증 결과]")
    print(json.dumps({
        "verification_passed": state.get("verification_passed"),
        "citation_validation_status": state.get("citation_validation_status"),
        "citations": state.get("citations", []),
        "review_notes": state.get("review_notes", []),
        "validation_errors": state.get("validation_errors", []),
        "citation_latency_ms": state.get("citation_latency_ms"),
    }, ensure_ascii=False, indent=2))

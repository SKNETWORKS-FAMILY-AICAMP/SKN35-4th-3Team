
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
    """Generator의 주장과 검색 문서의 근거를 검증합니다."""

    started = perf_counter()

    # Generator가 답변을 생성한 경우에만 검증
    if state.get("generation_status") != "answered_unverified":
        return {
            "citation_validation_status": "skipped",
            "citation_latency_ms": round(
                (perf_counter() - started) * 1000, 1
            ),
        }

    question = state.get("origin_query", "")
    answer = state.get("answer", "")
    claims = state.get("claims") or []
    sources = state.get("context_sources") or []
    documents = state.get("context_documents") or []

    # 출처 ID별 메타데이터
    source_map = {
        source["source_id"]: source
        for source in sources
        if isinstance(source, dict) and source.get("source_id")
    }

    # 출처 ID별 실제 문서
    document_map = {
        document["source_id"]: document
        for document in documents
        if isinstance(document, dict)
        and document.get("source_id")
        and isinstance(document.get("text"), str)
        and document["text"].strip()
    }

    errors = []

    # 1. Generator 출력 검증
    if not isinstance(answer, str) or not answer.strip():
        errors.append({"code": "missing_answer"})

    if not isinstance(claims, list) or not claims:
        errors.append({"code": "missing_claim_mapping"})

    else:
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append({
                    "code": "invalid_claim",
                    "claim_index": index,
                })
                continue

            text = claim.get("text")
            source_ids = claim.get("source_ids")

            if not isinstance(text, str) or not text.strip():
                errors.append({
                    "code": "invalid_claim_text",
                    "claim_index": index,
                })

            if not isinstance(source_ids, list) or not source_ids:
                errors.append({
                    "code": "claim_without_citation",
                    "claim_index": index,
                })
                continue

            if any(
                not isinstance(sid, str)
                or sid not in source_map
                or sid not in document_map
                for sid in source_ids
            ):
                errors.append({
                    "code": "unknown_citation",
                    "claim_index": index,
                })

    if not source_map or not document_map:
        errors.append({"code": "missing_context"})

    # 2. 기본 검증 결과
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

    if errors:
        result["citation_latency_ms"] = round(
            (perf_counter() - started) * 1000, 1
        )
        return result

    # 3. LLM 근거 검증
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
                "question": question,
                "answer": answer,
                "claims": claims,
                "documents": documents,
            }, ensure_ascii=False, default=str)),
        ])

        if not isinstance(response.content, str):
            raise ValueError("LLM 응답이 문자열이 아닙니다.")

        output = json.loads(response.content)

        if not isinstance(output, dict):
            raise ValueError("검증 결과는 JSON 객체여야 합니다.")

        evaluations = output.get("evaluations")

        if not isinstance(evaluations, list):
            raise ValueError("evaluations는 목록이어야 합니다.")

        if len(evaluations) != len(claims):
            raise ValueError(
                "주장과 평가 결과의 개수가 다릅니다."
            )

        if not isinstance(output.get("passed"), bool):
            raise ValueError("passed는 boolean이어야 합니다.")

        review_notes = output.get("review_notes", [])

        if not isinstance(review_notes, list):
            raise ValueError(
                "review_notes는 목록이어야 합니다."
            )

        # 4. 평가 결과 및 출처 ID 검증
        verified_ids = []
        all_supported = True

        for index, (claim, evaluation) in enumerate(
            zip(claims, evaluations)
        ):
            if not isinstance(evaluation, dict):
                raise ValueError(
                    f"{index}번 평가 결과가 객체가 아닙니다."
                )

            verdict = evaluation.get("verdict")
            evaluated_ids = evaluation.get("source_ids")

            if verdict not in (
                "supported",
                "partial",
                "unsupported",
            ):
                raise ValueError(
                    f"{index}번 평가의 verdict가 올바르지 않습니다."
                )

            if not isinstance(evaluated_ids, list):
                raise ValueError(
                    f"{index}번 평가의 source_ids가 목록이 아닙니다."
                )

            # Generator가 연결한 출처만 허용
            if any(
                not isinstance(sid, str)
                or sid not in claim["source_ids"]
                or sid not in document_map
                for sid in evaluated_ids
            ):
                raise ValueError(
                    f"{index}번 평가에 허용되지 않은 출처가 있습니다."
                )

            if verdict != "supported" or not evaluated_ids:
                all_supported = False
            else:
                verified_ids.extend(evaluated_ids)

        # 5. 최종 검증 통과 여부
        passed = (
            output["passed"] is True
            and all_supported
            and bool(verified_ids)
        )

        if passed:
            used_ids = list(dict.fromkeys(verified_ids))
            citations = [
                source_map[sid]
                for sid in used_ids
            ]
        else:
            citations = []

        result.update(
            generation_status=(
                "answered"
                if passed
                else "verification_failed"
            ),
            verification_passed=passed,
            citation_validation_status=(
                "passed"
                if passed
                else "failed"
            ),
            citations=citations,
            sources=citations,
            used_document_ids=list(dict.fromkeys(
                source.get("document_id")
                or source.get("point_id")
                for source in citations
                if source.get("document_id")
                or source.get("point_id")
            )),
            citation_evaluations=evaluations,
            review_notes=review_notes,
        )

        if not passed:
            result["validation_errors"] = [
                {"code": "unsupported_claim"}
            ]

    except Exception as error:
        result.update(
            generation_status="answered_unverified",
            verification_passed=False,
            citation_validation_status="error",
            validation_errors=[
                {"code": "citation_error"}
            ],
            review_notes=[
                "근거 검증을 완료하지 못했습니다."
            ],
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

    if not question:
        raise SystemExit("질문을 입력해주세요.")

    # 앞단계에서 사용하는 LLM
    llm = ChatOpenAI(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
    )

    # 1. Query Planner
    print("\n[1. Query Planner]")
    plan = query_planner(
        question=question,
        llm=llm,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    # 2. Query Rewrite
    print("\n[2. Query Rewrite]")
    rewritten = question

    if plan["needs_rewrite"]:
        rewritten = query_rewrite(
            question=question,
            llm=llm,
            planner_result=plan,
        )["rewritten_query"]

    print(rewritten)

    # 3. Multi-Query
    print("\n[3. Multi-Query]")
    expanded = []

    if plan["needs_expansion"]:
        expanded = multi_query(
            question=question,
            llm=llm,
            rewritten_query=rewritten,
            planner_result=plan,
            max_queries=3,
        )["queries"]

        for i, query in enumerate(expanded, start=1):
            print(f"확장 질문 {i}: {query}")

    queries = list(dict.fromkeys(
        q.strip()
        for q in [question, rewritten, *expanded]
        if isinstance(q, str) and q.strip()
    ))

    print("\n[최종 검색 질의]")
    print(json.dumps(
        queries,
        ensure_ascii=False,
        indent=4,
    ))

    # 4. Hybrid Search
    print("\n[4. Hybrid Search]")
    rankings = {}

    for index, query in enumerate(queries, start=1):
        found = hybrid_search(
            query=query,
            top_k=40,
        )

        rankings[f"q{index}_dense"] = found["dense_results"]
        rankings[f"q{index}_bm25"] = found["bm25_results"]

        print(
            f"질의 {index}: "
            f"Dense {len(found['dense_results'])}개, "
            f"BM25 {len(found['bm25_results'])}개"
        )

    # 5. RRF
    print("\n[5. RRF]")
    documents = rrf({
        "rankings": rankings
    })["documents"]

    print(f"통합 문서: {len(documents)}개")

    # 6. Rerank
    print("\n[6. Rerank]")
    documents = rerank(
        query=question,
        documents=documents,
        top_k=5,
        batch_size=8,
    )

    print(f"최종 문서: {len(documents)}개")

    print("\n[Generator에 전달되는 검색 문서]")

    for i, doc in enumerate(documents, start=1):
        print(f"\n--- 문서 {i} ---")
        print(
            "내용:",
            str(doc.get("text", ""))[:1500],
        )
        print(
            "메타데이터:",
            doc.get("metadata", doc.get("payload", {})),
        )

    # 7. Generator
    print("\n[7. Generator]")

    state = {
        "origin_query": question,
        "documents": documents,
    }

    state.update(generator(state))

    print("\n[생성 답변]")
    print(state["answer"])

    print("\n[생성 상태]")
    print(state["generation_status"])

    print("\n[주장과 근거]")
    print(json.dumps(
        state.get("claims", []),
        ensure_ascii=False,
        indent=2,
    ))

    # 8. Citation
    print("\n[8. Citation]")

    state.update(citation(state))

    print("\n[최종 답변]")
    print(state["answer"])

    print("\n[최종 상태]")
    print(state["generation_status"])

    print("\n[검증 결과]")
    print(json.dumps({
        "verification_passed": state.get(
            "verification_passed"
        ),
        "citation_validation_status": state.get(
            "citation_validation_status"
        ),
        "citations": state.get("citations", []),
        "used_document_ids": state.get(
            "used_document_ids", []
        ),
        "citation_evaluations": state.get(
            "citation_evaluations", []
        ),
        "review_notes": state.get(
            "review_notes", []
        ),
        "validation_errors": state.get(
            "validation_errors", []
        ),
        "citation_error": state.get(
            "citation_error"
        ),
        "citation_latency_ms": state.get(
            "citation_latency_ms"
        ),
    }, ensure_ascii=False, indent=2))

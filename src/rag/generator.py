"""검색 문서를 근거로 구조화된 생활법률 답변을 생성합니다."""

import json
import os
from time import perf_counter

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from src.const.prompt.generator_prompt import GENERATOR_PROMPT

load_dotenv()


def build_context(documents: list[dict]) -> dict:
    records = []
    sources = []

    # 상위 다섯개만 처리
    for document in documents:
        if len(records) >= 5:
            break

        if not isinstance(document, dict):
            continue

        content = document.get("text")
        if not isinstance(content, str) or not content.strip():
            continue

        metadata = (
            document.get("metadata")
            or document.get("payload")
            or {}
        )

        if not isinstance(metadata, dict):
            metadata = {}

        source_id = f"S{len(sources) + 1}"

        source = {
            "precedent_id": metadata.get("precedent_id"),
            "case_number": metadata.get("case_number"),
            "case_name": metadata.get("case_name"),
            "court": metadata.get("court"),
            "decision_date": metadata.get("decision_date"),

            "source_id": source_id,
            "point_id": document.get("point_id"),
            "document_id": metadata.get("document_id"),
            "chunk_id": metadata.get("chunk_id"),
            "source_name": (
                metadata.get("law_name")
                or metadata.get("source_name")
                or metadata.get("title")
            ),
            "source_url": metadata.get("source_url"),
            "document_type": metadata.get("document_type"),
            "article": (
                metadata.get("article_label")
                or metadata.get("article_number")
            ),
        }

        sources.append(source)
        records.append({**source, "text": content.strip()})

    return {
        "context": json.dumps(
            records, ensure_ascii=False, default=str
        ),
        "sources": sources,
        "documents": records,
    }


def validate_output(output: dict, sources: list[dict]) -> dict:
    """LLM 출력 형식과 출처 ID를 검증합니다."""

    if not isinstance(output, dict):
        raise TypeError("LLM 응답은 JSON 객체여야 합니다.")

    status = output.get("status")
    answer = output.get("answer")
    claims = output.get("claims")

    valid_statuses = {
        "answered",
        "insufficient_evidence",
        "needs_clarification",
        "conflicting_evidence",
    }

    if status not in valid_statuses:
        raise ValueError("올바르지 않은 생성 상태입니다.")

    if not isinstance(answer, str):
        raise TypeError("answer는 문자열이어야 합니다.")

    if not isinstance(claims, list):
        raise TypeError("claims는 목록이어야 합니다.")

    valid_ids = {source["source_id"] for source in sources}

    for claim in claims:
        if not isinstance(claim, dict):
            raise TypeError("claim은 객체여야 합니다.")

        text = claim.get("text")
        source_ids = claim.get("source_ids")

        if not isinstance(text, str) or not text.strip():
            raise ValueError("claim의 text가 올바르지 않습니다.")

        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError("claim에 근거 ID가 없습니다.")

        if any(
            not isinstance(source_id, str)
            or source_id not in valid_ids
            for source_id in source_ids
        ):
            raise ValueError("존재하지 않는 출처 ID가 포함되었습니다.")


    if status == "answered" and (
        not answer.strip() or not claims
    ):
        raise ValueError(
            "답변 완료 상태에는 answer와 claims가 필요합니다."
        )

    if status == "answered":
        # LLM이 따로 작성한 claims는 답변을 누락하거나 바꿔 쓸 수 있습니다.
        # 최종 답변 자체를 검증하여 문자 일치 오류와 검증 대상 누락을 방지합니다.
        claims = [{
            "text": answer.strip(),
            "source_ids": list(dict.fromkeys(
                sid for claim in claims for sid in claim["source_ids"]
            )),
        }]

    return {
        "status": status,
        "answer": answer.strip(),
        "claims": claims,
    }


def generator(state: dict, *, llm=None) -> dict:
    """LangGraph Generator 노드입니다."""

    started = perf_counter()

    question = state.get("origin_query")

    if not isinstance(question, str) or not question.strip():
        raise ValueError("문자열 origin_query가 필요합니다.")

    model_name = os.getenv("OPENAI_MODEL")

    if not model_name:
        raise ValueError(
            ".env에 OPENAI_MODEL을 설정해야 합니다."
        )

    response = {
        "answer": "검색된 자료에서 질문의 핵심 근거를 확인하지 못했습니다.",
        "sources": [],
        "claims": [],
        "generation_status": "insufficient_evidence",
        "model_name": model_name,
        "token_usage": {},
        "output_validation_error": None,
        "generation_attempts": 0,
    }

    prepared = build_context(
        documents=state.get("documents") or []
    )

    response.update(
        context_sources=prepared["sources"],
        context_documents=prepared["documents"],
    )

    if not prepared["sources"]:
        response["latency_ms"] = round(
            (perf_counter() - started) * 1000, 1
        )
        return response

    try:
        model = llm if llm is not None else ChatOpenAI(
            model=model_name,
            temperature=0,
            model_kwargs={
                "response_format": {"type": "json_object"}
            },
        )

        messages = [
            SystemMessage(content=GENERATOR_PROMPT),
            HumanMessage(
                content=(
                    f"사용자의 원본 질문:\n{question.strip()}\n\n"
                    f"검색 자료(JSON):\n{prepared['context']}"
                )
            ),
        ]

        # 형식 오류는 검사를 완화하지 않고 오류를 알려 한 번만 재생성합니다.
        for attempt in range(2):
            response["generation_attempts"] = attempt + 1
            message = model.invoke(messages)
            usage = getattr(message, "usage_metadata", None) or {}
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    response["token_usage"][key] = response["token_usage"].get(key, 0) + value
            try:
                if not isinstance(message.content, str):
                    raise ValueError("LLM 응답이 문자열이 아닙니다.")
                output = json.loads(message.content)
                validated = validate_output(output, prepared["sources"])
                break
            except (TypeError, ValueError) as error:
                if attempt == 1:
                    raise
                messages.extend([
                    message,
                    HumanMessage(content=(
                        f"출력 검증에 실패했습니다: {error}\n"
                        "같은 검색 자료만 사용하여 JSON 전체를 다시 작성하세요. "
                        "claims.text는 최종 answer에서 그대로 복사하고, "
                        "각 claim에는 자료에 존재하는 source_id를 연결하세요. "
                        "검사를 통과하기 위해 핵심 주장이나 근거를 임의로 삭제하지 마세요."
                    )),
                ])

        response.update(
            answer=validated["answer"],
            claims=validated["claims"],
            sources=[
                source
                for source in prepared["sources"]
                if source["source_id"] in {
                    source_id
                    for claim in validated["claims"]
                    for source_id in claim["source_ids"]
                }
            ],
            generation_status=(
                "answered_unverified"
                if validated["status"] == "answered"
                else validated["status"]
            ),
        )

    except (TypeError, ValueError) as error:
        response.update(
            answer="답변 형식을 확인할 수 없어 응답을 보류합니다.",
            generation_status="invalid_output",
            output_validation_error=str(error),
        )

    except Exception as error:
        response.update(
            answer="답변 생성 중 오류가 발생했습니다.",
            generation_status="llm_error",
            generation_error=str(error),
        )

    response["latency_ms"] = round(
        (perf_counter() - started) * 1000, 1
    )

    return response







if __name__ == "__main__":
    from src.rag.query_planner import query_planner
    from src.rag.query_rewrite import query_rewrite
    from src.rag.multi_query import multi_query
    from src.rag.hybrid_search import hybrid_search
    from src.rag.rrf import rrf
    from src.rag.rerank import rerank

    question = input("질문을 입력하세요: ").strip()
    if not question:
        raise SystemExit("질문을 입력해주세요.")

    # 앞단계에서 사용하는 LLM
    llm = ChatOpenAI(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
    )

    print("\n[1. Query Planner]")
    plan = query_planner(question=question, llm=llm)
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    print("\n[2. Query Rewrite]")
    rewritten = question
    if plan["needs_rewrite"]:
        rewritten = query_rewrite(
            question=question,
            llm=llm,
            planner_result=plan,
        )["rewritten_query"]
    print(rewritten)



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
    print(json.dumps(queries, ensure_ascii=False, indent=4))

    print("\n[4. Hybrid Search]")
    rankings = {}
    for index, query in enumerate(queries, start=1):
        found = hybrid_search(query=query, top_k=40)
        rankings[f"q{index}_dense"] = found["dense_results"]
        rankings[f"q{index}_bm25"] = found["bm25_results"]
        print(
            f"질의 {index}: "
            f"Dense {len(found['dense_results'])}개, "
            f"BM25 {len(found['bm25_results'])}개"
        )

    print("\n[5. RRF]")
    documents = rrf({"rankings": rankings})["documents"]
    print(f"통합 문서: {len(documents)}개")

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
        print("내용:", str(doc.get("text", ""))[:1500])
        print("메타데이터:", doc.get("metadata", doc.get("payload", {})))

    print("\n[7. Generator]")
    result = generator({
        "origin_query": question,
        "documents": documents,
    })

    print("\n[최종 답변]")
    print(result["answer"])

    print("\n[생성 상태]")
    print(result["generation_status"])

    print("\n[주장과 근거]")
    print(json.dumps(result["claims"], ensure_ascii=False, indent=2))

    if result.get("generation_error"):
        print("LLM 오류:", result["generation_error"])
    if result.get("output_validation_error"):
        print("출력 검증 오류:", result["output_validation_error"])

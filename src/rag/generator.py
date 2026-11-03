"""검색 문서를 근거로 구조화된 생활법률 답변을 생성합니다."""

import json
import os
from time import perf_counter

from langchain_core.messages import HumanMessage, SystemMessage

from src.const.model import OPENAI_LLM_MODEL, create_generator_llm
from src.const.prompt.generator_prompt import GENERATOR_PROMPT


def build_context(documents: list[dict], char_budget: int) -> dict[str, object]:
    if not isinstance(documents, list):
        raise TypeError("documents는 목록이어야 합니다.")
    if char_budget < 1000:
        raise ValueError("컨텍스트 문자 제한은 1000 이상이어야 합니다.")

    records: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    used = 0
    for document in documents:
        if not isinstance(document, dict):
            continue
        text = document.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        metadata = document.get("metadata") or document.get("payload") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        remaining = char_budget - used
        if remaining <= 0:
            break
        excerpt = text.strip()[:remaining]
        source = {
            "source_id": f"S{len(sources) + 1}",
            "point_id": document.get("point_id"),
            "document_id": metadata.get("document_id"),
            "chunk_id": metadata.get("chunk_id"),
            "source_name": metadata.get("law_name")
            or metadata.get("source_name")
            or metadata.get("title"),
            "source_url": metadata.get("source_url"),
            "document_type": metadata.get("document_type"),
            "article": metadata.get("article_label")
            or metadata.get("article_number"),
        }
        sources.append(source)
        records.append({**source, "text": excerpt})
        used += len(excerpt)

    return {
        "context": json.dumps(records, ensure_ascii=False),
        "sources": sources,
        "documents": records,
    }


def generator(state: dict, *, llm=None) -> dict[str, object]:
    started = perf_counter()
    question = state.get("origin_query")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("문자열 origin_query가 필요합니다.")

    response: dict[str, object] = {
        "answer": "검색된 자료에서 질문의 핵심 근거를 확인하지 못했습니다.",
        "sources": [],
        "claims": [],
        "generation_status": "insufficient_evidence",
        "model_name": OPENAI_LLM_MODEL,
        "token_usage": {},
    }
    prepared = build_context(
        state.get("documents") or [],
        int(os.getenv("GENERATOR_CONTEXT_CHARS", "12000")),
    )
    response.update(
        context_sources=prepared["sources"],
        context_documents=prepared["documents"],
    )
    if not prepared["sources"]:
        response["latency_ms"] = round((perf_counter() - started) * 1000, 1)
        return response

    model = llm or create_generator_llm()
    message = model.invoke(
        [
            SystemMessage(content=GENERATOR_PROMPT),
            HumanMessage(
                content=(
                    f"사용자의 원본 질문: {question.strip()}\n"
                    f"검색 자료(JSON):\n{prepared['context']}"
                )
            ),
        ]
    )
    response["token_usage"] = getattr(message, "usage_metadata", None) or {}
    try:
        output = json.loads(message.content)
        status = output["status"]
        answer = output["answer"]
        claims = output["claims"]
        if status not in {
            "answered",
            "insufficient_evidence",
            "needs_clarification",
            "conflicting_evidence",
        }:
            raise ValueError("알 수 없는 생성 상태입니다.")
        if not isinstance(answer, str) or not isinstance(claims, list):
            raise TypeError("Generator 응답 형식이 올바르지 않습니다.")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        response.update(
            answer="답변 형식을 확인할 수 없어 응답을 보류합니다.",
            generation_status="invalid_output",
            output_validation_error=str(error),
        )
    else:
        response.update(
            answer=answer.strip(),
            claims=claims,
            generation_status=(
                "generated_unverified" if status == "answered" else status
            ),
        )

    response["latency_ms"] = round((perf_counter() - started) * 1000, 1)
    return response

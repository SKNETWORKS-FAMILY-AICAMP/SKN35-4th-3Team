# State 클래스 정의
from typing import NotRequired, Annotated

from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from src.vectorstore import VectorSearchHit

class State(MessagesState):
    history : Annotated[list[str], add_messages]

    origin_query : str # 원본
    retry_count : int

    images: NotRequired[list[str]]        # base64 또는 URL 리스트

    rewrite_query: NotRequired[str] # 재작성
    queries : NotRequired[list[str]] # 멀티쿼리 용
    rankings: NotRequired[dict[str, list[VectorSearchHit]]]
    documents : NotRequired[list[dict]] # 가져온 문서

    is_valid: NotRequired[bool] # 생성에 전달할 문서가 있는지 확인한 결과(근거 검증 아님)
    validation_reason: NotRequired[str]
    score : NotRequired[float] # 분기체크 용

    answer : str # 답변

    # 검색 필터와 Generator 결과를 다음 노드로 전달합니다.
    filters: NotRequired[dict]
    sources: NotRequired[list[dict]]
    used_document_ids: NotRequired[list[str]]
    generation_status: NotRequired[str]
    generation_attempts: NotRequired[int]
    model_name: NotRequired[str]
    latency_ms: NotRequired[float]
    token_usage: NotRequired[dict]
    estimated_cost_usd: NotRequired[float | None]
    claims: NotRequired[list[dict]]
    source_validation_passed: NotRequired[bool]
    error_type: NotRequired[str | None]
    output_validation_error: NotRequired[str | None]
    rejected_output: NotRequired[object]
    context_tokens: NotRequired[int]
    context_sources: NotRequired[list[dict]]
    context_documents: NotRequired[list[dict]]
    context_omissions: NotRequired[list[dict]]

    # Citation 구조 검사 결과입니다. 의미적 Judge는 아직 실행하지 않습니다.
    citations: NotRequired[list[dict]]
    citation_validation_status: NotRequired[str]
    validation_errors: NotRequired[list[dict]]
    validation_warnings: NotRequired[list[dict]]
    citation_validity_rate: NotRequired[float | None]
    invalid_citation_count: NotRequired[int]
    missing_citation_count: NotRequired[int | None]
    citation_coverage: NotRequired[float | None]
    citation_support_rate: NotRequired[float | None]
    citation_latency_ms: NotRequired[float]
    judge_status: NotRequired[str]
    judge_input: NotRequired[dict | None]
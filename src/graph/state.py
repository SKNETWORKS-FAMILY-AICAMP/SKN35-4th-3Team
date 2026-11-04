# State 클래스 정의
from typing import Annotated, NotRequired, TypedDict

from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages

class State(MessagesState):
    history : Annotated[list[str], add_messages]

    origin_query : str # 원본
    retry_count : int

    images: NotRequired[list[str]]        # base64 또는 URL 리스트

    rewrite_query: NotRequired[str] # 재작성
    facts: NotRequired[list[str]]
    conditions: NotRequired[list[str]]
    intents: NotRequired[list[str]]
    needs_rewrite: NotRequired[bool]
    needs_expansion: NotRequired[bool]
    queries : NotRequired[list[str]] # 멀티쿼리 용
    rankings: NotRequired[dict[str, list[dict]]]
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

    verification_passed: NotRequired[bool]
    citation_evaluations: NotRequired[list[dict]]
    review_notes: NotRequired[list[str]]
    citation_error: NotRequired[str]


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

    # 판례 MCP 서브그래프 입출력입니다. 검색 중간값과 임베딩은 저장하지 않습니다.
    mcp_reference_candidate: NotRequired[dict[str, object] | None]
    mcp_top_n: NotRequired[int]
    mcp_candidate_limit: NotRequired[int]
    mcp_status: NotRequired[str]
    mcp_result: NotRequired[dict[str, object]]
    mcp_reference_precedent: NotRequired[dict[str, object]]
    mcp_similar_precedents: NotRequired[list[dict[str, object]]]
    mcp_documents: NotRequired[list[dict[str, object]]]
    mcp_excluded_precedents: NotRequired[list[dict[str, object]]]
    mcp_error: NotRequired[dict[str, object] | None]
    mcp_similarity_score: NotRequired[float]
    mcp_similarity_percent: NotRequired[str]


class MCPGraphState(TypedDict, total=False):
    """판례 MCP 서브그래프의 외부 입출력만 관리하는 State."""

    # 검색 입력
    user_query: str
    origin_query: str
    rewrite_query: str
    mcp_top_n: int
    mcp_candidate_limit: int

    # 특정 판례 비교 입력
    mcp_reference_candidate: dict[str, object] | None

    # MCP 처리 결과
    mcp_status: str
    mcp_result: dict[str, object]
    mcp_reference_precedent: dict[str, object]
    mcp_similar_precedents: list[dict[str, object]]
    mcp_excluded_precedents: list[dict[str, object]]
    mcp_error: dict[str, object] | None

    # 상위 Workflow 전달 데이터
    mcp_documents: list[dict[str, object]]
    documents: list[dict[str, object]]

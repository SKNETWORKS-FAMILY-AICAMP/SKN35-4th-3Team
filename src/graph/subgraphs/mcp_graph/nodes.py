"""공식 판례 유사도 검색 MCP Tool을 LangGraph 노드로 연결합니다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeAlias

from src.const.precedent import DEFAULT_CANDIDATE_LIMIT, DEFAULT_TOP_N
from src.tools.precedent_contracts import parse_tool_response


MCPToolCall: TypeAlias = Callable[..., str]
MCPNode: TypeAlias = Callable[[Mapping[str, object]], dict[str, object]]
MCPNodeAction: TypeAlias = Callable[[], dict[str, object]]


class MCPToolServiceLike(Protocol):
    """노드 생성에 필요한 판례 Tool 서비스 인터페이스."""

    find_similar_precedents: MCPToolCall
    compare_precedent_similarity: MCPToolCall


def _text(state: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"필수 입력이 없습니다: {', '.join(keys)}")


def _error_result(
    status: str,
    message: str,
    *,
    payload: dict[str, object] | None = None,
    include_reference: bool = False,
) -> dict[str, object]:
    """노드 오류를 상위 workflow가 기대하는 공통 State로 변환합니다."""

    result: dict[str, object] = {
        "mcp_status": status,
        "mcp_result": payload if payload is not None else {},
        "mcp_similar_precedents": [],
        "mcp_documents": [],
        "documents": [],
        "mcp_excluded_precedents": [],
        "mcp_error": {"message": message},
    }
    if include_reference:
        result["mcp_reference_precedent"] = {}
    return result


def _tool_result(
    payload: dict[str, object],
    documents: list[dict[str, object]],
    excluded: list[dict[str, object]],
) -> dict[str, object]:
    """MCP 응답을 검색·비교 노드의 공통 State로 변환합니다."""

    error = payload.get("error")
    return {
        "mcp_status": payload["status"],
        "mcp_result": payload,
        "mcp_similar_precedents": documents,
        "mcp_documents": documents,
        "documents": documents,
        "mcp_excluded_precedents": excluded,
        "mcp_error": dict(error) if isinstance(error, Mapping) else None,
    }


def _run(
    action: MCPNodeAction,
    *,
    include_reference: bool = False,
) -> dict[str, object]:
    """Tool 입력·응답 오류를 공통 State 오류로 변환합니다."""

    try:
        return action()
    except (TypeError, ValueError) as error:
        return _error_result(
            "invalid_request",
            str(error),
            include_reference=include_reference,
        )


def create_find_similar_precedents_node(
    find_similar: MCPToolCall,
    *,
    default_top_n: int = DEFAULT_TOP_N,
    default_candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> MCPNode:
    """사용자 질문을 Qdrant에서 검색하고 유사 판례를 State에 저장합니다."""

    if not callable(find_similar):
        raise TypeError("find_similar는 호출 가능한 함수여야 합니다.")

    def node(state: Mapping[str, object]) -> dict[str, object]:
        def action() -> dict[str, object]:
            query = _text(
                state,
                "user_query",
                "rewrite_query",
                "origin_query",
            )
            payload = parse_tool_response(
                find_similar(
                    query,
                    top_n=state.get("mcp_top_n", default_top_n),
                    candidate_limit=state.get(
                        "mcp_candidate_limit",
                        default_candidate_limit,
                    ),
                )
            )

            results = payload.get("results", [])
            excluded = payload.get("excluded", [])
            if not isinstance(results, list) or not isinstance(excluded, list):
                return _error_result(
                    "internal_error",
                    "MCP 판례 검색 응답 형식이 잘못됐습니다.",
                    payload=payload,
                )
            return _tool_result(payload, results, excluded)

        return _run(action)

    return node


def create_compare_precedent_similarity_node(
    compare_similarity: MCPToolCall,
    *,
    default_top_n: int = DEFAULT_TOP_N,
    default_candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> MCPNode:
    """기준 판례를 조회하고 유사 판례를 State에 저장합니다."""

    if not callable(compare_similarity):
        raise TypeError("compare_similarity는 호출 가능한 함수여야 합니다.")

    def node(state: Mapping[str, object]) -> dict[str, object]:
        def action() -> dict[str, object]:
            candidate = state.get("mcp_reference_candidate")
            if not isinstance(candidate, Mapping):
                raise ValueError("mcp_reference_candidate가 필요합니다.")

            payload = parse_tool_response(
                compare_similarity(
                    dict(candidate),
                    top_n=state.get("mcp_top_n", default_top_n),
                    candidate_limit=state.get(
                        "mcp_candidate_limit",
                        default_candidate_limit,
                    ),
                )
            )

            reference = payload.get("reference_precedent", {})
            similar = payload.get("similar_precedents", [])
            excluded = payload.get("excluded", [])
            if (
                not isinstance(reference, Mapping)
                or not isinstance(similar, list)
                or not isinstance(excluded, list)
            ):
                return _error_result(
                    "internal_error",
                    "MCP 판례 비교 응답 형식이 잘못됐습니다.",
                    payload=payload,
                    include_reference=True,
                )

            result = _tool_result(payload, similar, excluded)
            result["mcp_reference_precedent"] = dict(reference)
            return result

        return _run(action, include_reference=True)

    return node


def create_mcp_tool_nodes(service: MCPToolServiceLike) -> dict[str, MCPNode]:
    """테스트·외부 조립을 위해 서비스 메서드를 노드 이름에 매핑합니다."""

    return {
        "mcp_find_similar_precedents": create_find_similar_precedents_node(
            service.find_similar_precedents
        ),
        "mcp_compare_precedent_similarity": create_compare_precedent_similarity_node(
            service.compare_precedent_similarity
        ),
    }

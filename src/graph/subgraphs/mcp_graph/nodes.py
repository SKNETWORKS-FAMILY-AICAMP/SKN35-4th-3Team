"""공식 판례 유사도 검색 MCP Tool을 LangGraph 노드로 연결합니다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeAlias

from src.tools.precedent_mcp import parse_tool_response


MCPToolCall: TypeAlias = Callable[..., str]
MCPNode: TypeAlias = Callable[[Mapping[str, object]], dict[str, object]]


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


def create_find_similar_precedents_node(
    find_similar: MCPToolCall,
    *,
    default_top_n: int = 5,
    default_candidate_limit: int = 20,
) -> MCPNode:
    """사용자 질문을 공식 API에서 검색하고 유사 판례를 State에 저장합니다."""

    if not callable(find_similar):
        raise TypeError("find_similar는 호출 가능한 함수여야 합니다.")

    def node(state: Mapping[str, object]) -> dict[str, object]:
        try:
            query = _text(
                state,
                "user_query",
                "rewrite_query",
                "origin_query",
            )
        except ValueError as error:
            return {
                "mcp_status": "invalid_request",
                "mcp_result": {},
                "mcp_similar_precedents": [],
                "mcp_documents": [],
                "documents": [],
                "mcp_excluded_precedents": [],
                "mcp_error": {"message": str(error)},
            }
        try:
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
        except (TypeError, ValueError) as error:
            return {
                "mcp_status": "invalid_request",
                "mcp_result": {},
                "mcp_similar_precedents": [],
                "mcp_documents": [],
                "documents": [],
                "mcp_excluded_precedents": [],
                "mcp_error": {"message": str(error)},
            }

        results = payload.get("results", [])
        excluded = payload.get("excluded", [])
        if not isinstance(results, list) or not isinstance(excluded, list):
            return {
                "mcp_status": "internal_error",
                "mcp_result": payload,
                "mcp_similar_precedents": [],
                "mcp_documents": [],
                "documents": [],
                "mcp_excluded_precedents": [],
                "mcp_error": {"message": "MCP 판례 검증 응답 형식이 잘못됐습니다."},
            }
        error = payload.get("error")
        return {
            "mcp_status": payload["status"],
            "mcp_result": payload,
            "mcp_similar_precedents": results,
            "mcp_documents": results,
            "documents": results,
            "mcp_excluded_precedents": excluded,
            "mcp_error": dict(error) if isinstance(error, Mapping) else None,
        }

    return node


def create_compare_precedent_similarity_node(
    compare_similarity: MCPToolCall,
    *,
    default_top_n: int = 5,
    default_candidate_limit: int = 20,
) -> MCPNode:
    """기준 판례를 검증하고 유사 판례를 State에 저장합니다."""

    if not callable(compare_similarity):
        raise TypeError("compare_similarity는 호출 가능한 함수여야 합니다.")

    def node(state: Mapping[str, object]) -> dict[str, object]:
        candidate = state.get("mcp_reference_candidate")
        if not isinstance(candidate, Mapping):
            return {
                "mcp_status": "invalid_request",
                "mcp_result": {},
                "mcp_reference_precedent": {},
                "mcp_similar_precedents": [],
                "mcp_documents": [],
                "documents": [],
                "mcp_excluded_precedents": [],
                "mcp_error": {"message": "mcp_reference_candidate가 필요합니다."},
            }

        try:
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
        except (TypeError, ValueError) as error:
            return {
                "mcp_status": "invalid_request",
                "mcp_result": {},
                "mcp_reference_precedent": {},
                "mcp_similar_precedents": [],
                "mcp_documents": [],
                "documents": [],
                "mcp_excluded_precedents": [],
                "mcp_error": {"message": str(error)},
            }

        reference = payload.get("reference_precedent", {})
        similar = payload.get("similar_precedents", [])
        excluded = payload.get("excluded", [])
        if (
            not isinstance(reference, Mapping)
            or not isinstance(similar, list)
            or not isinstance(excluded, list)
        ):
            return {
                "mcp_status": "internal_error",
                "mcp_result": payload,
                "mcp_reference_precedent": {},
                "mcp_similar_precedents": [],
                "mcp_documents": [],
                "documents": [],
                "mcp_excluded_precedents": [],
                "mcp_error": {"message": "MCP 판례 비교 응답 형식이 잘못됐습니다."},
            }

        error = payload.get("error")
        return {
            "mcp_status": payload["status"],
            "mcp_result": payload,
            "mcp_reference_precedent": dict(reference),
            "mcp_similar_precedents": similar,
            "mcp_documents": similar,
            "documents": similar,
            "mcp_excluded_precedents": excluded,
            "mcp_error": dict(error) if isinstance(error, Mapping) else None,
        }

    return node


def create_mcp_tool_nodes(service: MCPToolServiceLike) -> dict[str, MCPNode]:
    """MCP Tool별 독립 노드를 3차 프로젝트와 같은 호출 형식으로 반환합니다."""

    return {
        "mcp_find_similar_precedents": create_find_similar_precedents_node(
            service.find_similar_precedents
        ),
        "mcp_compare_precedent_similarity": (
            create_compare_precedent_similarity_node(
                service.compare_precedent_similarity
            )
        ),
    }

"""판례 검색과 기준 판례 비교 중 하나를 실행하는 MCP 서브그래프.

일반 질문은 Qdrant 판례 검색 노드로, 기준 판례가 있는 요청은 비교 노드로
분기합니다. 비교 노드는 기준 판례가 Qdrant에 없을 때만 공식 API를 사용합니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache

from langgraph.graph import END, START, StateGraph

from src.const.precedent import DEFAULT_CANDIDATE_LIMIT, DEFAULT_TOP_N
from src.graph.subgraphs.mcp_graph.nodes import (
    MCPToolServiceLike,
    create_compare_precedent_similarity_node,
    create_find_similar_precedents_node,
)
from src.graph.subgraphs.mcp_graph.state import MCPGraphState
from src.tools.precedent_api import create_precedent_api_client
from src.tools.precedent_mcp import MCPToolService, create_tool_service
from src.tools.qdrant_precedent import create_precedent_retriever


@cache
def _default_mcp_service() -> MCPToolService:
    """MCP 분기가 실행될 때 판례 Tool 서비스를 한 번만 만듭니다."""

    return create_tool_service(
        create_precedent_api_client(),
        create_precedent_retriever(),
    )


def _find_similar_precedents(
    query: str,
    *,
    top_n: int = DEFAULT_TOP_N,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> str:
    return _default_mcp_service().find_similar_precedents(
        query,
        top_n=top_n,
        candidate_limit=candidate_limit,
    )


def _compare_precedent_similarity(
    candidate: Mapping[str, object],
    *,
    top_n: int = DEFAULT_TOP_N,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> str:
    return _default_mcp_service().compare_precedent_similarity(
        candidate,
        top_n=top_n,
        candidate_limit=candidate_limit,
    )


def _route_mcp_tool(state: MCPGraphState) -> str:
    """기준 판례 입력 여부에 따라 검색 또는 비교 흐름을 선택합니다."""

    if isinstance(state.get("mcp_reference_candidate"), Mapping):
        return "compare"
    return "search"


def build_mcp_graph(service: MCPToolServiceLike | None = None):
    """검색·비교 분기가 드러나는 판례 MCP 서브그래프를 만듭니다."""

    find_similar = (
        service.find_similar_precedents
        if service
        else _find_similar_precedents
    )
    compare_similarity = (
        service.compare_precedent_similarity
        if service
        else _compare_precedent_similarity
    )

    builder = StateGraph(MCPGraphState)
    builder.add_node(
        "mcp_find_similar_precedents",
        create_find_similar_precedents_node(find_similar),
    )
    builder.add_node(
        "mcp_compare_precedent_similarity",
        create_compare_precedent_similarity_node(compare_similarity),
    )
    builder.add_conditional_edges(
        START,
        _route_mcp_tool,
        {
            "search": "mcp_find_similar_precedents",
            "compare": "mcp_compare_precedent_similarity",
        },
    )
    builder.add_edge("mcp_find_similar_precedents", END)
    builder.add_edge("mcp_compare_precedent_similarity", END)
    return builder.compile()

"""3차 Qdrant 후보를 공식 API로 검증하는 독립 MCP 서브그래프."""

from __future__ import annotations

from functools import cache
from types import SimpleNamespace
from collections.abc import Mapping

from langgraph.graph import END, START, StateGraph

from src.graph.subgraphs.mcp_graph.nodes import (
    create_mcp_tool_nodes,
)
from src.graph.subgraphs.mcp_graph.state import MCPGraphState
from src.tools.precedent_mcp import (
    MCPToolService,
    create_tool_service,
)
from src.tools.precedent_api import create_precedent_api_client
from src.tools.qdrant_precedent import create_precedent_retriever


@cache
def _default_mcp_service() -> MCPToolService:
    return create_tool_service(
        create_precedent_api_client(),
        create_precedent_retriever(),
    )


def _find_similar_precedents(
    query: str,
    *,
    top_n: int = 5,
    candidate_limit: int = 20,
) -> str:
    return _default_mcp_service().find_similar_precedents(
        query,
        top_n=top_n,
        candidate_limit=candidate_limit,
    )


def _compare_precedent_similarity(
    candidate: Mapping[str, object],
    *,
    top_n: int = 5,
    candidate_limit: int = 20,
) -> str:
    return _default_mcp_service().compare_precedent_similarity(
        candidate,
        top_n=top_n,
        candidate_limit=candidate_limit,
    )


def _route_mcp_tool(state: MCPGraphState) -> str:
    if isinstance(state.get("mcp_reference_candidate"), Mapping):
        return "compare"
    return "search"


def build_mcp_graph(service: MCPToolService | None = None):
    """공유 workflow와 분리된 판례 검증 서브그래프를 만듭니다."""

    tool_service = service or SimpleNamespace(
        find_similar_precedents=_find_similar_precedents,
        compare_precedent_similarity=_compare_precedent_similarity,
    )
    nodes = create_mcp_tool_nodes(tool_service)
    builder = StateGraph(MCPGraphState)
    builder.add_node(
        "mcp_find_similar_precedents",
        nodes["mcp_find_similar_precedents"],
    )
    builder.add_node(
        "mcp_compare_precedent_similarity",
        nodes["mcp_compare_precedent_similarity"],
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

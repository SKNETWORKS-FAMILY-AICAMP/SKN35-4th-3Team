"""판례 검색·비교 MCP 서브그래프 전용 State."""

from __future__ import annotations

from typing import TypedDict


class MCPGraphState(TypedDict, total=False):
    # Inputs
    user_query: str
    origin_query: str
    rewrite_query: str
    mcp_top_n: int
    mcp_candidate_limit: int
    mcp_reference_candidate: dict[str, object]

    # Outputs
    mcp_status: str
    mcp_result: dict[str, object]
    mcp_reference_precedent: dict[str, object]
    mcp_similar_precedents: list[dict[str, object]]
    mcp_documents: list[dict[str, object]]
    documents: list[dict[str, object]]
    mcp_excluded_precedents: list[dict[str, object]]
    mcp_error: dict[str, object] | None

"""판례 검증 MCP 서브그래프 전용 State."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class MCPGraphState(TypedDict, total=False):
    user_query: str
    origin_query: str
    rewrite_query: str
    mcp_top_n: int
    mcp_candidate_limit: int
    mcp_reference_candidate: dict[str, object]

    mcp_status: str
    mcp_result: dict[str, object]
    mcp_reference_precedent: dict[str, object]
    mcp_similar_precedents: list[dict[str, object]]
    mcp_documents: list[dict[str, object]]
    documents: list[dict[str, object]]
    mcp_excluded_precedents: list[dict[str, object]]
    mcp_error: NotRequired[dict[str, object] | None]

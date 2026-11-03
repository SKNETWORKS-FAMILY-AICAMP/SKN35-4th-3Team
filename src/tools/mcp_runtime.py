"""공식 MCP SDK에 두 개의 판례 업무 Tool을 등록합니다.

이 모듈은 SDK Tool 등록과 서비스 위임만 담당합니다. Qdrant 판례 검색과
공식 API fallback 규칙은 각각의 업무 모듈에 위임합니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from src.const.precedent import DEFAULT_CANDIDATE_LIMIT, DEFAULT_TOP_N
from src.tools.precedent_api import create_precedent_api_client
from src.tools.precedent_mcp import MCPToolService, create_tool_service
from src.tools.qdrant_precedent import create_precedent_retriever


SERVER_NAME = "SKN35-4th-3Team Precedent Verification"


def create_mcp_server(service: MCPToolService) -> MCPServer:
    """주입받은 판례 검색 서비스를 읽기 전용 MCP Tool로 제공합니다."""

    server = MCPServer(SERVER_NAME)

    @server.tool()
    def find_similar_precedents(
        query: str,
        top_n: int = DEFAULT_TOP_N,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> str:
        """Qdrant에서 질문과 유사한 판례 Top-N을 반환합니다."""

        return service.find_similar_precedents(
            query,
            top_n=top_n,
            candidate_limit=candidate_limit,
        )

    @server.tool()
    def compare_precedent_similarity(
        candidate: dict[str, Any],
        top_n: int = DEFAULT_TOP_N,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> str:
        """기준 판례와 유사한 판례를 Qdrant 점수순으로 반환합니다."""

        return service.compare_precedent_similarity(
            candidate,
            top_n=top_n,
            candidate_limit=candidate_limit,
        )

    return server


def create_team_mcp_server(
    env_path: str | Path | None = None,
) -> MCPServer:
    """판례 전용 검색기와 API fallback 클라이언트를 조립합니다."""

    api = create_precedent_api_client(env_path)
    retriever = create_precedent_retriever(env_path)
    return create_mcp_server(create_tool_service(api, retriever))


def main() -> int:
    create_team_mcp_server().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

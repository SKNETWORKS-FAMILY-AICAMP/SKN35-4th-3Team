"""공식 MCP SDK에 판례 검색·검증 Tool을 등록합니다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from src.tools.precedent_api import create_precedent_api_client
from src.tools.precedent_mcp import (
    MCPToolService,
    create_tool_service,
)
from src.tools.qdrant_precedent import create_precedent_retriever


SERVER_NAME = "SKN35-4th-3Team Precedent Verification"


def create_mcp_server(service: MCPToolService) -> MCPServer:
    """주입받은 판례 검증 서비스를 읽기 전용 MCP Tool로 제공합니다."""

    server = MCPServer(SERVER_NAME)

    @server.tool()
    def health_check() -> str:
        """MCP와 공식 판례 API의 연결 상태를 확인합니다."""

        return service.health_check()

    @server.tool()
    def get_precedent_detail(precedent_id: str) -> str:
        """판례정보일련번호로 공식 판례 상세 내용을 조회합니다."""

        return service.get_precedent_detail(precedent_id)

    @server.tool()
    def verify_precedent(
        candidate: dict[str, Any],
        query: str | None = None,
    ) -> str:
        """사용자가 제시한 판례 한 건을 공식 판례와 대조합니다."""

        return service.verify_precedent(candidate, query=query)

    @server.tool()
    def verify_precedent_candidates(
        candidates: list[dict[str, Any]],
        query: str | None = None,
        top_n: int = 5,
        candidate_limit: int = 20,
    ) -> str:
        """여러 판례 후보를 공식 검증하고 MCP 유사도순으로 반환합니다."""

        return service.verify_precedent_candidates(
            candidates,
            query=query,
            top_n=top_n,
            candidate_limit=candidate_limit,
        )

    @server.tool()
    def find_similar_precedents(
        query: str,
        top_n: int = 5,
        candidate_limit: int = 20,
    ) -> str:
        """3차 Qdrant 후보를 공식 API로 검증해 유사 판례 Top-N을 찾습니다."""

        return service.find_similar_precedents(
            query,
            top_n=top_n,
            candidate_limit=candidate_limit,
        )

    @server.tool()
    def compare_precedent_similarity(
        candidate: dict[str, Any],
        top_n: int = 5,
        candidate_limit: int = 20,
    ) -> str:
        """기준 판례와 유사한 공식 판례를 Qdrant 점수순으로 반환합니다."""

        return service.compare_precedent_similarity(
            candidate,
            top_n=top_n,
            candidate_limit=candidate_limit,
        )

    return server


def create_team_mcp_server(
    env_path: str | Path | None = None,
) -> MCPServer:
    api = create_precedent_api_client(env_path)
    retriever = create_precedent_retriever(env_path)
    return create_mcp_server(create_tool_service(api, retriever))


def main() -> int:
    create_team_mcp_server().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

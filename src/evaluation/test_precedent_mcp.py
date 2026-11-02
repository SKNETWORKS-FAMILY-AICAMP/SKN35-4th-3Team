"""판례 검색·검증 MCP의 네트워크 없는 최소 단위 테스트."""

from __future__ import annotations

import asyncio
import unittest

from src.graph.subgraphs.mcp_graph import build_mcp_graph, create_mcp_tool_nodes
from src.tools.precedent_api import OfficialPrecedent, PrecedentApiError
from src.tools.precedent_mcp import create_tool_service, parse_tool_response
from src.tools.mcp_runtime import create_mcp_server
from src.tools.precedent_verification import (
    find_similar_precedents,
    verify_precedent_candidates,
)


class FakePrecedentApi:
    def __init__(self, precedents: list[OfficialPrecedent]) -> None:
        self.precedents = {
            precedent.precedent_id: precedent for precedent in precedents
        }

    def search(self, query: str, *, display: int = 20, page: int = 1):
        terms = [term for term in query.split() if term]
        return tuple(
            precedent
            for precedent in self.precedents.values()
            if any(
                term
                in " ".join(
                    (
                        precedent.case_number,
                        precedent.case_name,
                        precedent.holding,
                        precedent.summary,
                        precedent.reference_articles,
                        precedent.body,
                    )
                )
                for term in terms
            )
        )[:display]

    def get_detail(self, precedent_id: str) -> OfficialPrecedent:
        try:
            return self.precedents[precedent_id]
        except KeyError:
            raise PrecedentApiError(
                "not_found",
                "공식 API에서 판례를 찾지 못했습니다.",
            ) from None

    def health_check(self) -> bool:
        return True


REAL_CASES = [
    OfficialPrecedent(
        precedent_id="147076",
        case_name="건물명도등·임대차보증금반환등",
        case_number="2005가합100279,2006가합62053",
        decision_date="2007-05-31",
        court="서울중앙지법",
        holding="임대차계약 종료 후 임대차보증금 반환 범위",
        summary="임차인은 건물을 인도하고 임대인은 보증금을 반환한다.",
        reference_articles="민법 제618조",
        body="임대차보증금 반환과 건물 인도 의무의 관계를 판단한 판례이다.",
        source_url="https://www.law.go.kr/LSW/precInfoP.do?precSeq=147076",
    ),
    OfficialPrecedent(
        precedent_id="233339",
        case_name="협박",
        case_number="2020노3067",
        decision_date="2022-07-07",
        court="서울중앙지방법원",
        holding="임대차 분쟁 과정에서 이루어진 협박의 성립 여부",
        summary="해악의 고지가 협박죄에 해당하는지 판단한다.",
        reference_articles="형법 제283조",
        body="임대차 당사자 사이의 발언이 협박에 해당하는지 판단한 판례이다.",
        source_url="https://www.law.go.kr/LSW/precInfoP.do?precSeq=233339",
    ),
]


class PrecedentVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakePrecedentApi(REAL_CASES)

    def test_returns_only_verified_cases_in_similarity_order(self):
        result = verify_precedent_candidates(
            self.api,
            [
                {
                    "precedent_id": "999999",
                    "similarity_score": 0.99,
                    "retrieval_rank": 1,
                    "score_type": "rerank",
                },
                {
                    "precedent_id": "147076",
                    "case_number": "2005가합100279",
                    "similarity_score": 0.91,
                    "retrieval_rank": 2,
                    "score_type": "rerank",
                },
                {
                    "precedent_id": "233339",
                    "case_number": "2020노3067",
                    "similarity_score": 0.81,
                    "retrieval_rank": 3,
                    "score_type": "rerank",
                },
            ],
            top_n=2,
            candidate_limit=3,
        )

        self.assertEqual(
            [item["precedent_id"] for item in result["results"]],
            ["147076", "233339"],
        )
        self.assertEqual(
            [item["verified_rank"] for item in result["results"]],
            [1, 2],
        )
        self.assertEqual(result["excluded"][0]["verification_status"], "not_found")

    def test_keeps_similarity_score_separate_from_verification(self):
        result = verify_precedent_candidates(
            self.api,
            [
                {
                    "precedent_id": "147076",
                    "case_name": "다른 사건명",
                    "similarity_score": 0.999,
                    "score_type": "rerank",
                }
            ],
            top_n=1,
        )

        self.assertEqual(result["results"], [])
        self.assertEqual(
            result["excluded"][0]["verification_status"],
            "partial_match",
        )
        self.assertEqual(result["excluded"][0]["similarity_score"], 0.999)

    def test_rejects_mixed_score_types(self):
        with self.assertRaisesRegex(ValueError, "동일한 score_type"):
            verify_precedent_candidates(
                self.api,
                [
                    {
                        "precedent_id": "147076",
                        "similarity_score": 0.9,
                        "score_type": "rerank",
                    },
                    {
                        "precedent_id": "233339",
                        "similarity_score": 0.8,
                        "score_type": "dense_cosine",
                    },
                ],
                top_n=1,
            )

    def test_can_verify_by_case_number_without_precedent_id(self):
        result = verify_precedent_candidates(
            self.api,
            [
                {
                    "case_number": "2020노3067",
                    "similarity_score": 0.73,
                    "score_type": "rerank",
                }
            ],
            top_n=1,
        )

        self.assertEqual(result["results"][0]["precedent_id"], "233339")
        self.assertIsNone(result["results"][0]["candidate"]["precedent_id"])
        self.assertEqual(
            result["results"][0]["official"]["precedent_id"],
            "233339",
        )
        self.assertEqual(
            result["results"][0]["verification_status"],
            "verified",
        )


class MCPToolServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = create_tool_service(FakePrecedentApi(REAL_CASES))

    def test_service_returns_safe_invalid_request(self):
        payload = parse_tool_response(
            self.service.verify_precedent_candidates([], top_n=0)
        )
        self.assertEqual(payload["status"], "invalid_request")

    def test_mcp_only_search_returns_official_similarity_ranking(self):
        result = find_similar_precedents(
            self.service.api,
            "임대차 보증금 반환",
            top_n=2,
            candidate_limit=2,
        )

        self.assertEqual(result["score_type"], "official_text_similarity")
        self.assertEqual(result["results"][0]["precedent_id"], "147076")
        self.assertGreater(
            result["results"][0]["mcp_similarity_score"],
            result["results"][1]["mcp_similarity_score"],
        )
        self.assertEqual(
            [item["similarity_rank"] for item in result["results"]],
            [1, 2],
        )

    def test_natural_language_query_falls_back_to_official_keyword_search(self):
        class ExactSearchApi(FakePrecedentApi):
            def search(self, query: str, *, display: int = 20, page: int = 1):
                if query == "보증금":
                    return (self.precedents["147076"],)
                return ()

        result = find_similar_precedents(
            ExactSearchApi(REAL_CASES),
            "보증금 관련 판례 알려줘",
            top_n=1,
            candidate_limit=2,
        )

        self.assertEqual(result["results"][0]["precedent_id"], "147076")
        self.assertIn("보증금", result["search_queries"])

    def test_service_does_not_require_rag_candidate_or_score(self):
        payload = parse_tool_response(
            self.service.find_similar_precedents(
                "임대차 보증금 반환",
                top_n=1,
                candidate_limit=2,
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["results"][0]["precedent_id"], "147076")
        self.assertNotIn("external_similarity_score", payload["results"][0])

    def test_claimed_precedent_is_compared_with_official_text(self):
        payload = parse_tool_response(
            self.service.verify_precedent(
                {
                    "precedent_id": "147076",
                    "case_number": "2005가합100279",
                    "comparison_text": "임대차가 끝나면 보증금을 반환해야 한다.",
                }
            )
        )

        result = payload["result"]
        self.assertEqual(result["verification_status"], "verified")
        self.assertIsNone(result["external_similarity_score"])
        self.assertGreater(result["mcp_similarity_score"], 0)
        self.assertEqual(
            result["mcp_score_type"],
            "official_identity_and_text_similarity",
        )

    def test_graph_uses_service_without_django_or_rag_imports(self):
        result = build_mcp_graph(self.service).invoke(
            {
                "user_query": "임대차 보증금 반환",
                "mcp_top_n": 1,
                "mcp_candidate_limit": 2,
            }
        )
        self.assertEqual(result["mcp_status"], "success")
        self.assertEqual(
            result["mcp_similar_precedents"][0]["precedent_id"],
            "147076",
        )

    def test_mcp_server_registers_direct_similarity_tool(self):
        server = create_mcp_server(self.service)
        tool_names = {
            tool.name for tool in asyncio.run(server.list_tools())
        }

        self.assertEqual(
            tool_names,
            {
                "health_check",
                "get_precedent_detail",
                "verify_precedent",
                "verify_precedent_candidates",
                "find_similar_precedents",
                "compare_precedent_similarity",
            },
        )

    def test_third_project_factory_names_build_nodes_and_graph_lazily(self):
        nodes = create_mcp_tool_nodes(self.service)

        self.assertEqual(
            set(nodes),
            {
                "mcp_find_similar_precedents",
                "mcp_compare_precedent_similarity",
            },
        )
        self.assertIsNotNone(build_mcp_graph())


if __name__ == "__main__":
    unittest.main()

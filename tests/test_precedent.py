"""판례 조회가 Qdrant 필터와 공식 API 보완 경로를 지키는지 확인합니다."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.tools.precedent_api import OfficialPrecedent
from src.tools.precedent_mcp import create_tool_service, parse_tool_response
from src.tools.qdrant_precedent import QdrantPrecedentRetriever


def _point(identifier, document_type, score, *, precedent_id=None):
    return SimpleNamespace(
        id=identifier,
        score=score,
        payload={
            "document_type": document_type,
            "precedent_id": precedent_id,
            "document_id": f"{document_type}:{precedent_id or identifier}",
            "case_name": "보증금 반환 판례",
            "text": "임대차 종료와 보증금 반환에 관한 판례 본문",
        },
    )


class PrecedentCallTests(unittest.TestCase):
    def _service(self, client, api):
        retriever = QdrantPrecedentRetriever(
            client=client,
            embed_query=lambda _query: [0.1] * 1536,
            collection_name="SKN35-4th-3Team",
        )
        return create_tool_service(api, retriever)

    def test_question_search_returns_only_precedents_without_official_api(self):
        client, api = Mock(), Mock()
        client.query_points.return_value = SimpleNamespace(points=[
            _point("law-point", "law", 0.99),
            _point("precedent-point", "precedent", 0.81, precedent_id="147076"),
        ])
        result = parse_tool_response(
            self._service(client, api).find_similar_precedents(
                "임대차 보증금 반환", top_n=2, candidate_limit=3
            )
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual([row["precedent_id"] for row in result["results"]], ["147076"])
        self.assertEqual(result["results"][0]["similarity_percent"], "81.0%")
        options = client.query_points.call_args.kwargs
        self.assertEqual(options["collection_name"], "SKN35-4th-3Team")
        self.assertEqual(options["query_filter"].must[0].match.value, "precedent")
        api.search.assert_not_called()
        api.get_detail.assert_not_called()

    def test_missing_reference_uses_official_api_then_qdrant_candidates(self):
        client, api = Mock(), Mock()
        client.scroll.return_value = ([], None)
        client.query_points.return_value = SimpleNamespace(points=[
            _point("other-point", "precedent", 0.72, precedent_id="233339")
        ])
        api.get_detail.return_value = OfficialPrecedent(
            precedent_id="147076",
            case_name="기준 판례",
            case_number="2005가합100279",
            body="임대차보증금 반환 범위를 판단하였다.",
        )
        result = parse_tool_response(
            self._service(client, api).compare_precedent_similarity(
                {"precedent_id": "147076"}, top_n=1, candidate_limit=2
            )
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["reference_precedent"]["lookup_source"], "official_api")
        self.assertEqual(result["similar_precedents"][0]["precedent_id"], "233339")
        api.get_detail.assert_called_once_with("147076")


if __name__ == "__main__":
    unittest.main()

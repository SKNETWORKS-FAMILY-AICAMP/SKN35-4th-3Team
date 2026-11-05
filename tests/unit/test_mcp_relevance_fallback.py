"""Qdrant 판례 관련도에 따른 공식 API fallback을 검증합니다."""

import unittest
from unittest.mock import Mock

from src.const.precedent import MIN_QDRANT_PRECEDENT_SIMILARITY
from src.tools.precedent_api import OfficialPrecedent, PrecedentApiError
from src.tools.precedent_mcp import create_tool_service, parse_tool_response


def _candidate(identifier: str, score: float) -> dict[str, object]:
    return {
        "precedent_id": identifier,
        "case_name": f"Qdrant 판례 {identifier}",
        "similarity_score": score,
        "source_url": (
            "https://www.law.go.kr/LSW/precInfoP.do?precSeq="
            f"{identifier}"
        ),
    }


def _official(identifier: str) -> OfficialPrecedent:
    return OfficialPrecedent(
        precedent_id=identifier,
        case_name="임대인의 실제 거주와 계약갱신 거절",
        case_number=f"2026다{identifier}",
        holding="임대인의 실제 거주 의사와 입증책임을 판단한 사건",
        body="임대인이 실제 거주를 이유로 계약갱신을 거절한 사안이다.",
        source_url=(
            "https://www.law.go.kr/LSW/precInfoP.do?precSeq="
            f"{identifier}"
        ),
    )


class MCPRelevanceFallbackTests(unittest.TestCase):
    def test_high_similarity_results_skip_official_api(self):
        api, retriever = Mock(), Mock()
        retriever.search_candidates.return_value = (
            _candidate("100001", 0.81),
            _candidate("100002", 0.72),
        )

        result = parse_tool_response(
            create_tool_service(api, retriever).find_similar_precedents(
                "임대차 보증금 반환",
                top_n=2,
                candidate_limit=3,
            )
        )

        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["qdrant_qualified_count"], 2)
        self.assertEqual(result["qdrant_low_similarity_count"], 0)
        api.search.assert_not_called()

    def test_low_similarity_results_trigger_official_api(self):
        api, retriever = Mock(), Mock()
        retriever.search_candidates.return_value = (
            _candidate("100001", 0.45),
            _candidate("100002", 0.42),
        )
        official = _official("200001")
        api.search.return_value = (official,)
        api.get_detail.return_value = official

        result = parse_tool_response(
            create_tool_service(api, retriever).find_similar_precedents(
                "임대인이 실제 거주를 이유로 계약갱신을 거절한 판례",
                top_n=1,
                candidate_limit=3,
            )
        )

        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["qdrant_qualified_count"], 0)
        self.assertEqual(result["qdrant_low_similarity_count"], 2)
        self.assertEqual(result["results"][0]["precedent_id"], "200001")
        self.assertEqual(result["results"][0]["ranking_source"], "official_api")
        self.assertTrue(all(
            item["exclusion_reason"] == "low_similarity"
            for item in result["excluded"]
            if item.get("precedent_id") in {"100001", "100002"}
        ))
        api.search.assert_called()

    def test_threshold_score_is_kept(self):
        api, retriever = Mock(), Mock()
        retriever.search_candidates.return_value = (
            _candidate("100001", MIN_QDRANT_PRECEDENT_SIMILARITY),
        )

        result = parse_tool_response(
            create_tool_service(api, retriever).find_similar_precedents(
                "판례 검색",
                top_n=1,
                candidate_limit=2,
            )
        )

        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["results"][0]["precedent_id"], "100001")
        api.search.assert_not_called()

    def test_api_error_keeps_only_qualified_qdrant_results(self):
        api, retriever = Mock(), Mock()
        retriever.search_candidates.return_value = (
            _candidate("100001", 0.81),
            _candidate("100002", 0.44),
        )
        api.search.side_effect = PrecedentApiError(
            "connection_error",
            "공식 판례 API 연결에 실패했습니다.",
        )

        result = parse_tool_response(
            create_tool_service(api, retriever).find_similar_precedents(
                "임대차 판례",
                top_n=2,
                candidate_limit=3,
            )
        )

        self.assertEqual(
            [item["precedent_id"] for item in result["results"]],
            ["100001"],
        )
        self.assertEqual(result["fallback_error"]["code"], "connection_error")
        self.assertEqual(result["qdrant_low_similarity_count"], 1)


if __name__ == "__main__":
    unittest.main()

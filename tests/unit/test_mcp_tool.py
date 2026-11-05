"""MCP Tool 응답이 상위 답변 단계에 전달되는 계약을 확인합니다."""

import unittest
from unittest.mock import Mock

from src.graph.subgraphs.mcp_graph.nodes import (
    create_compare_precedent_similarity_node,
    create_find_similar_precedents_node,
)
from src.rag.generator import build_context
from src.tools.precedent_contracts import serialize_tool_response


PRECEDENT = {
    "precedent_id": "147076",
    "document_id": "precedent:147076",
    "case_name": "임대차보증금 반환",
    "body": "임대차 종료 후 보증금 반환 범위를 판단한 판례이다.",
    "source_url": "https://www.law.go.kr/LSW/precInfoP.do?precSeq=147076",
    "similarity_score": 0.8,
    "similarity_rank": 1,
    "ranking_source": "qdrant",
    "score_type": "qdrant_cosine_similarity",
}


class MCPToolNodeTests(unittest.TestCase):
    def test_search_result_is_generator_evidence_and_keeps_original_candidate(self):
        tool = Mock(return_value=serialize_tool_response(
            "success", results=[PRECEDENT], excluded=[]
        ))
        node = create_find_similar_precedents_node(tool)
        result = node({"origin_query": "임대차 보증금 판례"})

        self.assertEqual(result["mcp_status"], "success")
        self.assertEqual(result["mcp_documents"], [PRECEDENT])
        self.assertEqual(result["documents"][0]["text"], PRECEDENT["body"])
        self.assertEqual(
            result["documents"][0]["metadata"]["document_type"], "precedent"
        )
        self.assertEqual(
            result["documents"][0]["metadata"]["source_name"],
            PRECEDENT["case_name"],
        )
        context = build_context(result["documents"])
        self.assertEqual(context["documents"][0]["text"], PRECEDENT["body"])
        self.assertEqual(context["sources"][0]["source_url"], PRECEDENT["source_url"])
        self.assertEqual(
            result["documents"][0]["metadata"]["ranking_source"],
            "qdrant",
        )
        tool.assert_called_once_with(
            "임대차 보증금 판례", top_n=5, candidate_limit=20
        )

    def test_compare_tool_returns_reference_and_similar_precedents(self):
        reference = {"precedent_id": "147076", "case_name": "기준 판례"}
        similar = {**PRECEDENT, "precedent_id": "233339"}
        excluded = {
            **PRECEDENT,
            "exclusion_reason": "reference_precedent",
        }
        tool = Mock(return_value=serialize_tool_response(
            "success",
            reference_precedent=reference,
            similar_precedents=[similar],
            excluded=[excluded],
        ))
        node = create_compare_precedent_similarity_node(tool)
        result = node({"mcp_reference_candidate": {"precedent_id": "147076"}})

        self.assertEqual(result["mcp_reference_precedent"], reference)
        self.assertEqual(result["mcp_similar_precedents"], [similar])
        self.assertEqual(result["mcp_excluded_precedents"], [excluded])
        self.assertEqual(result["documents"][0]["text"], similar["body"])
        tool.assert_called_once_with(
            {"precedent_id": "147076"}, top_n=5, candidate_limit=20
        )

    def test_missing_question_returns_safe_invalid_request(self):
        tool = Mock()
        result = create_find_similar_precedents_node(tool)({})
        self.assertEqual(result["mcp_status"], "invalid_request")
        self.assertEqual(result["documents"], [])
        tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import os
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from src.graph.nodes.summary_node import is_summary_request, summary_node
from src.graph.workflow import build_workflow, route_image
from src.rag.rerank import _load_rerank, get_rerank
from src.graph.nodes.precedent_query_node import is_precedent_lookup, precedent_query_node
from src.graph.workflow import route_start
from src.const.llm_settings import llm_settings
from src.graph.nodes.rerank_node import rerank_node


class FollowupTests(SimpleTestCase):
    def test_deployment_reasoning_and_output_budgets(self):
        with patch.dict(os.environ, {"LLM_REASONING_EFFORT": "minimal", "GENERATOR_MAX_OUTPUT_TOKENS": "3200"}, clear=True):
            self.assertEqual(llm_settings("gpt-5-nano", "generator"), {"reasoning_effort": "minimal", "max_tokens": 3200})
            self.assertEqual(llm_settings("gpt-4.1-mini"), {})
            self.assertEqual(llm_settings("gpt-5.1"), {})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm_settings("gpt-5-nano"), {})

    @patch("src.graph.nodes.rerank_node.rerank", return_value=[])
    def test_reranker_limits_only_the_highest_ranked_candidates(self, reranker):
        documents = [{"point_id": str(index)} for index in range(40)]
        with patch.dict(os.environ, {"RERANK_CANDIDATE_LIMIT": "20"}):
            rerank_node({"origin_query": "질문", "documents": documents})
        self.assertEqual(reranker.call_args.kwargs["documents"], documents[:20])
        self.assertEqual(len(documents), 40)

    @patch("src.graph.subgraphs.mcp_graph.graph._default_mcp_service")
    @patch("src.graph.nodes.generator_node.generator")
    def test_precedent_graph_receives_context_and_returns_source_metadata(self, generator, service):
        source = {"source_url": "https://www.law.go.kr/LSW/precInfoP.do?precSeq=100001", "case_name": "보증금 반환"}
        service.return_value.find_similar_precedents.return_value = json.dumps({
            "status": "success", "results": [{**source, "body": "판례 본문"}], "excluded": [],
        })
        generator.side_effect = lambda state: {
            "answer": "판례 안내", "generation_status": "answered",
            "sources": [state["documents"][0]["metadata"]],
        }
        result = build_workflow().invoke({
            "origin_query": "관련해서 판례를 갖고와줘", "images": [], "answer": "", "documents": [],
            "messages": [HumanMessage(content="집주인이 보증금을 반환하지 않아요")],
        })
        query = service.return_value.find_similar_precedents.call_args.args[0]
        self.assertIn("보증금", query)
        self.assertEqual(result["sources"][0]["source_url"], source["source_url"])
        self.assertEqual(result["mcp_status"], "success")
        generator.assert_not_called()

    @patch("src.graph.workflow.create_llm")
    def test_explicit_precedent_lookup_bypasses_law_only_route(self, llm):
        question = "관련해서 판례를 갖고와줘"
        self.assertTrue(is_precedent_lookup(question))
        self.assertEqual(route_start({"origin_query": question}), "mcp")
        llm.assert_not_called()

    def test_precedent_followup_uses_user_facts_not_previous_ai_guess(self):
        result = precedent_query_node({
            "origin_query": "관련해서 판례를 갖고와줘",
            "messages": [
                HumanMessage(content="집주인이 보증금을 반환하지 않고 연락이 안 돼요"),
                AIMessage(content="이사 직무집행정지 가처분에 관한 답변"),
                HumanMessage(content="요약해줘"),
            ],
        })
        self.assertIn("보증금", result["rewrite_query"])
        self.assertNotIn("직무집행정지", result["rewrite_query"])
        self.assertNotIn("요약해줘", result["rewrite_query"])

    def test_independent_precedent_query_does_not_inherit_unrelated_topic(self):
        query = "부당해고 판례를 찾아줘"
        result = precedent_query_node({"origin_query": query, "messages": [HumanMessage(content="임대차 보증금 문의")]})
        self.assertEqual(result["rewrite_query"], query)

    def test_only_pure_rephrasing_uses_summary_route(self):
        for question in ["요약해줘", "무슨소리야? 요약해줘", "방금 답변을 간단히 요약해줘", "쉽게 설명해줘"]:
            self.assertTrue(is_summary_request(question), question)
        for question in ["민법 제536조를 요약해줘", "요약해줘. 그리고 손해배상도 가능한가요?", "집주인이 연락이 안 돼"]:
            self.assertFalse(is_summary_request(question), question)

    @patch("src.graph.workflow.create_llm")
    def test_summary_bypasses_classifier_and_search(self, llm):
        self.assertEqual(route_image({"origin_query": "요약해줘"}), "summary")
        llm.assert_not_called()

    @patch("src.graph.nodes.summary_node.create_llm")
    def test_summary_uses_complete_previous_answer(self, llm):
        llm.return_value.invoke.return_value = SimpleNamespace(content="요약 결과")
        answer = "조건을 확인하세요. " * 100
        result = summary_node({"origin_query": "요약해줘", "messages": [AIMessage(content=answer)]})
        self.assertEqual(result["answer"], "요약 결과")
        self.assertIn(answer, llm.return_value.invoke.call_args.args[0][0].content)

    @patch("src.graph.nodes.summary_node.create_llm")
    def test_summary_keeps_previous_law_and_precedent_sources(self, llm):
        llm.return_value.invoke.return_value = SimpleNamespace(content="요약 결과")
        source = {"source_name": "법령", "source_url": "https://www.law.go.kr/법령/주택임대차보호법"}
        result = summary_node({"origin_query": "요약해줘", "messages": [AIMessage(content="이전 답변", additional_kwargs={"sources": [], "retrieved_sources": [source]})]})
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["context_sources"], [source])

    @patch("src.graph.nodes.summary_node.create_llm")
    def test_graph_summary_restores_serialized_session_messages(self, llm):
        llm.return_value.invoke.return_value = SimpleNamespace(content="보증금 반환 요약")
        result = build_workflow().invoke({
            "origin_query": "요약해줘", "images": [], "answer": "", "documents": [],
            "messages": [{"role": "assistant", "content": "보증금 반환은 계약 종료 등 요건을 확인해야 합니다."}],
        })
        self.assertEqual(result["answer"], "보증금 반환 요약")

    @patch("src.graph.nodes.summary_node.create_llm")
    def test_missing_history_does_not_run_model(self, llm):
        self.assertEqual(summary_node({"messages": []})["generation_status"], "needs_context")
        llm.assert_not_called()

    @patch("src.rag.rerank.torch.set_num_threads")
    @patch("src.rag.rerank.CrossEncoder")
    def test_concurrent_first_requests_load_only_one_reranker(self, model, threads):
        _load_rerank.cache_clear()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                loaded = list(pool.map(lambda _: get_rerank(), range(2)))
            self.assertIs(loaded[0], loaded[1])
            model.assert_called_once()
        finally:
            _load_rerank.cache_clear()

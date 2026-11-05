from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage

from src.graph.nodes.summary_node import is_summary_request, summary_node
from src.graph.workflow import build_workflow, route_image
from src.rag.rerank import _load_rerank, get_rerank


class FollowupTests(SimpleTestCase):
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

import json
import os
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from src.rag.generator import generator


class GeneratorOutputTests(unittest.TestCase):
    def run_generator(self, outputs):
        model = Mock()
        model.invoke.side_effect = [
            AIMessage(content=json.dumps(output, ensure_ascii=False))
            for output in outputs
        ]
        state = {
            "origin_query": "신청 요건은?",
            "documents": [{"text": "신청 요건에 관한 근거", "metadata": {}}],
        }
        with patch.dict(os.environ, {"OPENAI_MODEL": "test-model"}):
            result = generator(state, llm=model)
        return result, model

    def output(self, claim, source="S1"):
        return {
            "status": "answered",
            "answer": "근거에 따른 신청 요건입니다.",
            "claims": [{"text": claim, "source_ids": [source]}],
        }

    def test_paraphrased_claim_checks_actual_answer_without_retry(self):
        result, model = self.run_generator([
            self.output("답변에 없는 문장"),
        ])
        self.assertEqual(result["generation_status"], "answered_unverified")
        self.assertEqual(result["generation_attempts"], 1)
        self.assertEqual(result["claims"][0]["text"], result["answer"])
        self.assertEqual(model.invoke.call_count, 1)

    def test_unclaimed_answer_sentence_is_also_sent_for_verification(self):
        output = self.output("근거에 따른 신청 요건입니다.")
        output["answer"] += " 근거가 없는 추가 주장입니다."
        result, _ = self.run_generator([output])
        self.assertIn("근거가 없는 추가 주장입니다.", result["claims"][0]["text"])
        self.assertEqual(result["generation_status"], "answered_unverified")

    def test_repeated_invalid_source_is_rejected_with_reason(self):
        invalid = self.output("근거에 따른 신청 요건입니다.", "S99")
        result, model = self.run_generator([invalid, invalid])
        self.assertEqual(result["generation_status"], "invalid_output")
        self.assertIn("존재하지 않는 출처", result["output_validation_error"])
        self.assertEqual(model.invoke.call_count, 2)
        self.assertEqual(result["sources"], [])

    def test_valid_output_does_not_retry(self):
        result, model = self.run_generator([
            self.output("근거에 따른 신청 요건입니다."),
        ])
        self.assertEqual(result["generation_status"], "answered_unverified")
        self.assertEqual(model.invoke.call_count, 1)


if __name__ == "__main__":
    unittest.main()

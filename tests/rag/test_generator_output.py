import json
import os
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from src.rag.citation import citation
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


class CitationOutputTests(unittest.TestCase):
    def run_citation(self, *, source_ids, verdict="supported", passed=True):
        model = Mock()
        model.invoke.return_value = AIMessage(content=json.dumps({
            "passed": passed,
            "evaluations": [{
                "claim": "임대인의 실제 거주 의사에 관한 주장",
                "verdict": verdict,
                "source_ids": source_ids,
            }],
            "review_notes": [],
        }, ensure_ascii=False))
        state = {
            "origin_query": "임대인의 실제 거주 의사는 누가 입증하나요?",
            "answer": "임대인이 실제 거주 의사를 입증해야 합니다.",
            "claims": [{
                "text": "임대인이 실제 거주 의사를 입증해야 합니다.",
                "source_ids": ["S1"],
            }],
            "context_sources": [
                {
                    "source_id": "S1",
                    "document_id": "precedent:1",
                    "source_name": "허용 판례",
                },
                {
                    "source_id": "S2",
                    "document_id": "precedent:2",
                    "source_name": "다른 판례",
                },
            ],
            "context_documents": [
                {
                    "source_id": "S1",
                    "document_id": "precedent:1",
                    "text": "임대인의 실제 거주 의사에 대한 증명책임은 임대인에게 있다.",
                },
                {
                    "source_id": "S2",
                    "document_id": "precedent:2",
                    "text": "다른 법률 쟁점에 관한 판례이다.",
                },
            ],
            "generation_status": "answered_unverified",
        }
        with patch.dict(os.environ, {"OPENAI_MODEL": "test-model"}):
            result = citation(state, llm=model)
        request = json.loads(model.invoke.call_args.args[0][1].content)
        return result, request

    def test_allowed_source_passes_and_scopes_documents(self):
        result, request = self.run_citation(source_ids=["S1"])

        self.assertEqual(result["generation_status"], "answered")
        self.assertEqual(result["citations"][0]["source_id"], "S1")
        self.assertEqual(request["claims"][0]["allowed_source_ids"], ["S1"])
        self.assertEqual(
            [document["source_id"] for document in request["claims"][0]["documents"]],
            ["S1"],
        )

    def test_unexpected_source_is_filtered_with_warning(self):
        result, _ = self.run_citation(source_ids=["S1", "S2"])

        self.assertEqual(result["generation_status"], "answered")
        self.assertEqual(result["citations"][0]["source_id"], "S1")
        self.assertEqual(
            result["citation_evaluations"][0]["source_ids"],
            ["S1"],
        )
        self.assertEqual(
            result["validation_warnings"][0]["code"],
            "unexpected_source_ids",
        )

    def test_only_unexpected_source_fails_without_citation_error(self):
        result, _ = self.run_citation(source_ids=["S2"])

        self.assertEqual(result["generation_status"], "verification_failed")
        self.assertEqual(result["citation_validation_status"], "failed")
        self.assertEqual(result["citations"], [])
        self.assertNotIn("citation_error", result)

if __name__ == "__main__":
    unittest.main()

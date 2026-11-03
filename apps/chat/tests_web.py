import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse


class ChatWebTests(TestCase):
    def test_index_describes_chat_api(self):
        response = self.client.get(reverse("chat:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "생활법률 상담")
        self.assertContains(response, "/api/chat/")

    def test_chat_requires_question(self):
        response = self.client.post(reverse("chat:api"), {})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "invalid_request")

    @patch("src.rag.pdf_loader.pdf_to_images", return_value=[b"png-page"])
    @patch("src.graph.workflow.build_workflow")
    def test_pdf_is_converted_and_passed_to_workflow(self, workflow, convert):
        graph = workflow.return_value
        graph.invoke.return_value = {
            "answer": "확인했습니다.",
            "generation_status": "answered",
            "sources": [],
            "citations": [],
        }
        upload = SimpleUploadedFile(
            "contract.pdf",
            b"%PDF-test",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse("chat:api"),
            {"question": "계약 내용을 확인해 주세요.", "files": [upload]},
        )

        self.assertEqual(response.status_code, 200)
        convert.assert_called_once()
        state = graph.invoke.call_args.args[0]
        self.assertEqual(state["origin_query"], "계약 내용을 확인해 주세요.")
        self.assertEqual(len(state["images"]), 1)

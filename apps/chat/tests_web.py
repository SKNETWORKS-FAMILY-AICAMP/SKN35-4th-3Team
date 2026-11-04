import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse


class ChatWebTests(TestCase):
    def test_home_links_to_chat(self):
        response = self.client.get(reverse("chat:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/home.html")
        self.assertContains(response, f'href="{reverse("chat:index")}"')
        self.assertNotContains(response, 'id="chat-form"')

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

    @override_settings(CHAT_MAX_IMAGE_FILES=3)
    @patch("src.graph.workflow.build_workflow")
    def test_three_images_are_allowed(self, workflow):
        graph = workflow.return_value
        graph.invoke.return_value = {
            "answer": "확인했습니다.",
            "generation_status": "answered",
            "sources": [],
            "citations": [],
        }
        uploads = [
            SimpleUploadedFile(
                f"evidence-{index}.png",
                b"png-image",
                content_type="image/png",
            )
            for index in range(3)
        ]

        response = self.client.post(
            reverse("chat:api"),
            {"question": "첨부 이미지를 확인해 주세요.", "files": uploads},
        )

        self.assertEqual(response.status_code, 200)
        state = graph.invoke.call_args.args[0]
        self.assertEqual(len(state["images"]), 3)

    @override_settings(CHAT_MAX_IMAGE_FILES=3)
    @patch("src.graph.workflow.build_workflow")
    def test_four_images_are_rejected(self, workflow):
        uploads = [
            SimpleUploadedFile(
                f"evidence-{index}.jpg",
                b"jpeg-image",
                content_type="image/jpeg",
            )
            for index in range(4)
        ]

        response = self.client.post(
            reverse("chat:api"),
            {"question": "첨부 이미지를 확인해 주세요.", "files": uploads},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "invalid_request")
        self.assertIn("최대 3장", response.json()["error"])
        workflow.assert_not_called()

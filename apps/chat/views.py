"""Django 상담 화면과 LLM workflow 요청 진입점."""

import base64
import json
import logging
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from src.rag.pdf_loader import attachment_to_images


logger = logging.getLogger(__name__)


@require_GET
def index(request):
    """Django 기반 생활법률 상담 화면을 표시합니다."""

    return render(
        request,
        "chat/index.html",
        {
            "chat_endpoint": "/api/chat/",
            "pdf_max_pages": settings.CHAT_PDF_MAX_PAGES,
        },
    )


def _question(request) -> str:
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError as error:
            raise ValueError("JSON 요청 형식이 올바르지 않습니다.") from error
        value = payload.get("question", "")
    else:
        value = request.POST.get("question", "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("question은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _is_image_attachment(uploaded) -> bool:
    """PDF를 제외한 PNG·JPG·JPEG 첨부인지 확인합니다."""

    content_type = uploaded.content_type or ""
    suffix = uploaded.name.lower().rsplit(".", 1)[-1]
    if content_type == "application/pdf" or suffix == "pdf":
        return False
    return content_type in {"image/png", "image/jpeg"} or suffix in {
        "png",
        "jpg",
        "jpeg",
    }


def _attachment_images(request) -> list[str]:
    uploads = list(request.FILES.getlist("files"))
    image_count = sum(_is_image_attachment(uploaded) for uploaded in uploads)
    if image_count > settings.CHAT_MAX_IMAGE_FILES:
        raise ValueError(
            f"이미지는 최대 {settings.CHAT_MAX_IMAGE_FILES}장까지 첨부할 수 있습니다."
        )

    images: list[str] = []
    for uploaded in uploads:
        if uploaded.size > settings.CHAT_UPLOAD_MAX_BYTES:
            raise ValueError(
                f"첨부파일은 {settings.CHAT_UPLOAD_MAX_BYTES}바이트 이하여야 합니다."
            )
        content = uploaded.read()
        converted = attachment_to_images(
            uploaded.name,
            uploaded.content_type or "",
            content,
            max_pdf_pages=settings.CHAT_PDF_MAX_PAGES,
        )
        images.extend(
            base64.b64encode(image).decode("ascii") for image in converted
        )
    return images


@require_POST
def chat(request):
    """텍스트·이미지·PDF 입력을 4차 LangGraph workflow로 전달합니다."""

    try:
        question = _question(request)
        images = _attachment_images(request)
    except (ValueError, RuntimeError) as error:
        return JsonResponse(
            {"status": "invalid_request", "error": str(error)},
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    from src.graph.workflow import build_workflow

    thread_id = request.session.get("chat_thread_id")
    if not thread_id:
        thread_id = uuid4().hex
        request.session["chat_thread_id"] = thread_id

    try:
        result = build_workflow().invoke(
            {
                "origin_query": question,
                "retry_count": 0,
                "images": images,
                "answer": "",
                "documents": [],
            },
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception:
        logger.exception("상담 workflow 실행 중 오류")
        return JsonResponse(
            {
                "status": "service_error",
                "error": "상담 처리 중 오류가 발생했습니다.",
            },
            status=503,
            json_dumps_params={"ensure_ascii": False},
        )

    print(
        "\n[답변 상태 추적]",
        json.dumps(
            {
                "question": question,
                "graph_generation_status": result.get("generation_status"),
                "response_status": result.get("generation_status") or "answered",
                "answered_by_fallback": not bool(result.get("generation_status")),
                "citation_validation_status": result.get("citation_validation_status"),
                "validation_errors": result.get("validation_errors", []),
                "output_validation_error": result.get("output_validation_error"),
                "generation_attempts": result.get("generation_attempts"),
                "citations": result.get("citations", []),

                # 실제 생성한 답변
                "answer": result.get("answer", ""),

                # 답변의 주장과 연결된 출처 ID
                "claims": result.get("claims", []),

                # Generator·Citation에 전달한 근거 본문
                "context_documents": result.get("context_documents", []),

                # Citation의 상세 검증 결과
                "verification_passed": result.get("verification_passed"),
                "citation_evaluations": result.get("citation_evaluations", []),
                "review_notes": result.get("review_notes", []),
                "citation_error": result.get("citation_error"),
                "mcp_status": result.get("mcp_status"),
                "mcp_error": result.get("mcp_error"),
                "mcp_result": result.get("mcp_result"),
                "documents": result.get("documents", []),

            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )

    return JsonResponse(
        {
            "status": result.get("generation_status") or "answered",
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "citations": result.get("citations", []),
            "mcp_similarity_percent": result.get("mcp_similarity_percent"),
            "mcp_similar_precedents": result.get(
                "mcp_similar_precedents",
                [],
            ),
        },
        json_dumps_params={"ensure_ascii": False},
    )

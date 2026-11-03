"""상담 첨부 PDF를 멀티모달 모델 입력용 이미지로 변환합니다."""

MAX_PDF_PAGES = 5
DEFAULT_DPI = 120


def pdf_to_images(
    pdf_bytes: bytes,
    *,
    max_pages: int = MAX_PDF_PAGES,
    dpi: int = DEFAULT_DPI,
) -> list[bytes]:
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise ValueError("PDF 내용이 비어 있습니다.")
    if not 1 <= max_pages <= 20:
        raise ValueError("PDF 페이지 제한은 1~20이어야 합니다.")
    if not 72 <= dpi <= 300:
        raise ValueError("PDF 변환 DPI는 72~300이어야 합니다.")

    try:
        import pymupdf
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "PDF 첨부를 사용하려면 `uv sync`로 PyMuPDF를 설치하세요."
        ) from error

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("암호로 보호된 PDF는 사용할 수 없습니다.")
        page_count = min(len(document), max_pages)
        return [
            document[index].get_pixmap(dpi=dpi, alpha=False).tobytes("png")
            for index in range(page_count)
        ]
    finally:
        document.close()


def attachment_to_images(
    filename: str,
    content_type: str,
    content: bytes,
    *,
    max_pdf_pages: int = MAX_PDF_PAGES,
) -> list[bytes]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if content_type == "application/pdf" or suffix == "pdf":
        return pdf_to_images(content, max_pages=max_pdf_pages)
    if content_type in {"image/png", "image/jpeg"} or suffix in {
        "png",
        "jpg",
        "jpeg",
    }:
        return [content]
    raise ValueError("PNG, JPG, JPEG, PDF 파일만 첨부할 수 있습니다.")

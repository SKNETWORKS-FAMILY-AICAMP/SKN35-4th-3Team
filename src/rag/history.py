"""대화 메시지를 검색·분류 프롬프트용 문자열로 정리합니다."""

IMAGE_MARKER = "\n\n[첨부 이미지 설명]"
ROLE_NAMES = {"human": "사용자", "ai": "상담사"}


def format_history(messages, max_turns: int = 3, max_chars: int = 400) -> str:
    recent = (messages or [])[-max_turns * 2 :]
    lines: list[str] = []
    for message in recent:
        role = ROLE_NAMES.get(getattr(message, "type", None))
        if role is None:
            continue
        content = getattr(message, "content", "")
        text = content if isinstance(content, str) else str(content)
        text = text.split(IMAGE_MARKER)[0].strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "…"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)

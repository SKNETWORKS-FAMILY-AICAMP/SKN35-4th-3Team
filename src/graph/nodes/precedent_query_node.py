"""명시적인 판례 검색 요청을 감지하고 후속 질문의 검색 주제를 복원합니다."""

import re

from src.graph.nodes.summary_node import is_summary_request


def is_precedent_lookup(question: str) -> bool:
    text = re.sub(r"\s+", "", question)
    return bool(re.search(r"(?:판례|판결문|재판례).*(?:찾|검색|조회|가져|갖고|갖다|보여|제시)", text))


def precedent_query_node(state):
    question = state["origin_query"].strip()
    compact = re.sub(r"\s+", "", question)
    contextual = bool(re.match(r"(?:관련|그|이(?:와|에|사안|사건|내용|문제)|위|앞서|방금|비슷|같은)", compact))
    generic = bool(re.fullmatch(r"(?:판례|판결문)(?:를|을)?(?:찾아줘|찾아주세요|검색해줘|가져와줘|갖고와줘|보여줘)[.!?]*", compact))
    if contextual or generic:
        previous_questions = [
            str(message.content).split("\n\n[첨부 이미지 설명]")[0][:2000]
            for message in state.get("messages") or []
            if getattr(message, "type", None) == "human"
            and not is_summary_request(str(message.content))
            and not is_precedent_lookup(str(message.content))
        ][-2:]
        if previous_questions:
            question = "\n".join(previous_questions) + "\n관련 판례 검색 요청: " + question
    return {"rewrite_query": question}

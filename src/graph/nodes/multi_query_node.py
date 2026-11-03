from src.graph.state import State
from src.rag.multi_query import multi_query


def multi_query_node(state: State):
    # 재작성 질문이 있으면 사용하고, 없으면 원본 질문을 사용합니다.
    question = state.get("rewrite_query") or state["origin_query"]
    # 질문 확장과 검색을 실행하고, 질문 목록과 질문별 결과를 State에 저장합니다.
    response = multi_query(question, filters=state.get("filters"))
    # print(response)
    return response
from src.graph.state import State
from src.const.model import create_llm
from src.rag.history import format_history
from src.rag.query_rewrite import query_rewrite
# 노드 입니다.

# state에 따라서 노드의 상태를 정의합니다.

def route_after_rewrite(state:State):
    if state["needs_expansion"]:
        return "multi_query"      # 둘 다 true → 순차 진행
    return "retriever"

def query_rewrite_node(state:State):
    # query_rewrite.py에서 정의한 함수의 리턴값만 받아서 다룰 예정
    query = state["origin_query"]
    history = format_history(state.get("messages"))
    response = query_rewrite(
        question=query,
        llm=create_llm(),
        history=history,
    )

    return {
        'rewrite_query': response['rewritten_query'],
        "retry_count": state.get("retry_count", 0) + 1,
    }

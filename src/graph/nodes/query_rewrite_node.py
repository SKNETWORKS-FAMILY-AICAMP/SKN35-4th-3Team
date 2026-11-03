from src.graph.state import State
from src.rag.query_rewriter import query_rewriter
from src.history import format_history
# 노드 입니다.

# state에 따라서 노드의 상태를 정의합니다.

def query_rewrite_node(state:State):
    # query_rewrite.py에서 정의한 함수의 리턴값만 받아서 다룰 예정
    query = state["origin_query"]
    history = format_history(state.get("messages"))
    response = query_rewriter(text=query, history=history)

    return {'rewrite_query': response, "retry_count": state["retry_count"] + 1}
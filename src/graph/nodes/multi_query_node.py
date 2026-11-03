from src.graph.state import State
from src.const.model import create_llm
from src.rag.multi_query import multi_query


def multi_query_node(state: State):
    # 재작성 질문이 있으면 사용하고, 없으면 원본 질문을 사용합니다.
    return multi_query(
        question=state["origin_query"],
        llm=create_llm(),
        rewritten_query=state.get("rewrite_query"),
    )

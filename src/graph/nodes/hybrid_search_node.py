"""Multi-Query 결과를 Dense·BM25 후보 목록으로 검색합니다."""

from src.graph.state import State
from src.rag.hybrid_search import hybrid_search


def hybrid_search_node(state: State):

    queries = state.get("queries") or [
        state.get("rewrite_query") or state["origin_query"]
    ]
    rankings: dict[str, list[dict]] = {}
    for index, query in enumerate(queries):
        result = hybrid_search(query, top_k=20)
        rankings[f"query_{index}_dense"] = result["dense_results"]
        rankings[f"query_{index}_bm25"] = result["bm25_results"]
    return {"rankings": rankings}

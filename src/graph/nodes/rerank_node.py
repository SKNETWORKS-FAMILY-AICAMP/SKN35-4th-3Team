# from src.graph.state import State
# from src.rag.rerank import rerank

# def rerank_node(state: State):
#     documents = rerank(
#         query=state["origin_query"],
#         documents=state.get("documents") or [],
#         top_k=5,
#     )

#     # 최종 문서들의 Cross-Encoder 점수 중 최고점
#     best_score = max(
#         (doc["rerank_score"] for doc in documents),
#         default=None,
#     )

#     return {
#         "documents": documents,
#         "score": best_score,
#     }



from src.graph.state import State
from src.rag.rerank import rerank


def rerank_node(state: State):
    documents = rerank(
        query=state["origin_query"],
        documents=state.get("documents") or [],
        top_k=5,
    )

    return {
        "documents": documents,
        "score": max(
            (doc["rerank_score"] for doc in documents),
            default=None,
        ),
    }

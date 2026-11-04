from src.graph.state import State
from src.rag.rerank import rerank
# 노드 입니다.
# Precision@K 로 평가 진행
# state에 따라서 노드의 상태를 정의합니다.
# rerank 평가 후 다시 query_rewrite

# def rerank_node(state: State):
#     t = {}
#     response = rerank(state, top_k=5, timings=t)
#     print("best:", t.get("best_score"), "min:", t.get("min_score"), "filtered_out:", t.get("filtered_out_count"))
#     return {"documents": response["documents"], "score": response["score"]}



def rerank_node(state: State):
    documents = rerank(
        query=state["origin_query"],
        documents=state.get("documents") or [],
        top_k=5,
    )

    # 최종 문서들의 Cross-Encoder 점수 중 최고점
    best_score = max(
        (doc["rerank_score"] for doc in documents),
        default=None,
    )

    return {
        "documents": documents,
        "score": best_score,
    }
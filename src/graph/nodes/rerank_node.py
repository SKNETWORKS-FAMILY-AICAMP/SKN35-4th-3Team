from src.graph.state import State
from src.rag.rerank import rerank
# 노드 입니다.
# Precision@K 로 평가 진행
# state에 따라서 노드의 상태를 정의합니다.
# rerank 평가 후 다시 query_rewrite

def rerank_node(state: State):
    t = {}
    response = rerank(state, top_k=5, timings=t)
    print("best:", t.get("best_score"), "min:", t.get("min_score"), "filtered_out:", t.get("filtered_out_count"))
    return {"documents": response["documents"], "score": response["score"]}
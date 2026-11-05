from langgraph.graph import StateGraph, START, END
from src.graph.state import State
from src.graph.nodes.query_planner_node import query_planner_node , route_after_planning
from src.graph.nodes.multi_query_node import multi_query_node
from src.graph.nodes.hybrid_search_node import hybrid_search_node
from src.graph.nodes.query_rewrite_node import query_rewrite_node , route_after_rewrite
from src.graph.nodes.rrf_node import rrf_node
from src.graph.nodes.rerank_node import rerank_node
from src.graph.nodes.validation_node import validation_node
from src.graph.nodes.generator_node import generator_node
from src.graph.timing import timed

def route_after_validation(state: State):
    if state["is_valid"]:
        return "generator"
    else:
        return "query_rewrite"

def build_rag_graph():
    builder = StateGraph(State)

    print("RAG 진입")
    
    # 노드 생성
    builder.add_node("planner", timed(query_planner_node, "planner"))
    builder.add_node("query_rewrite", timed(query_rewrite_node, "query_rewrite"))
    builder.add_node("multi_query", timed(multi_query_node, "multi_query"))
    builder.add_node("hybrid_search", timed(hybrid_search_node, "hybrid_search"))
    builder.add_node("rrf", rrf_node)
    builder.add_node("rerank", timed(rerank_node, "rerank"))
    builder.add_node("validation", validation_node)
    builder.add_node("generator", timed(generator_node, "generator"))

    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", route_after_planning,
        {
            "query_rewrite": "query_rewrite",
            "multi_query": "multi_query",
            "hybrid_search": "hybrid_search",
        }
    )

    builder.add_conditional_edges("query_rewrite", route_after_rewrite,
        {
            "multi_query": "multi_query",
            "hybrid_search": "hybrid_search",
        }
    )

    builder.add_edge("multi_query", "hybrid_search")
    builder.add_edge("hybrid_search", "rrf")
    builder.add_edge("rrf", "rerank")
    builder.add_edge("rerank", "validation")
    builder.add_conditional_edges( "validation", route_after_validation,
        {
            "generator": "generator",
            "query_rewrite": "query_rewrite",
        }
    )

    builder.add_edge("generator", END)

    return builder.compile()

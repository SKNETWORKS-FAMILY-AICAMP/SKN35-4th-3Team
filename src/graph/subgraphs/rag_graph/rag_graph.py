from langgraph.graph import StateGraph, START, END
from src.graph.state import State
from src.graph.nodes.multi_query_node import multi_query_node
from src.graph.nodes.retriever_node import retriever_node
from src.graph.nodes.query_rewrite_node import query_rewrite_node
from src.graph.nodes.rrf_node import rrf_node
from src.graph.nodes.rerank_node import rerank_node
from src.graph.nodes.validation_node import validation_node
from src.graph.nodes.generator_node import generator_node

def route_after_validation(state: State):
    if state["is_valid"]:
        return "generator"
    else:
        return "query_rewrite"

def build_rag_graph():
    builder = StateGraph(State)

    print("RAG 진입")
    
    # 노드 생성
    builder.add_node("query_rewrite" , query_rewrite_node)
    builder.add_node("multi_query" , multi_query_node)
    builder.add_node("retriever" , retriever_node)
    builder.add_node("rrf" , rrf_node)
    builder.add_node("rerank" , rerank_node)
    builder.add_node("validation" , validation_node)
    builder.add_node("generator" , generator_node)

    builder.add_edge(START, "query_rewrite")
    builder.add_edge("query_rewrite", "multi_query")
    builder.add_edge("multi_query", "retriever")
    builder.add_edge("retriever", "rrf")
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

from langgraph.graph import StateGraph, START, END
from src.graph.state import State
from src.graph.nodes.image_node import image_node
from src.graph.subgraphs.rag_graph.rag_graph import build_rag_graph
from src.graph.subgraphs.mcp_graph.mcp_graph import build_mcp_graph
from src.graph.nodes.general_node import general_node
from src.const.model import create_llm
from src.const.prompt.classify_prompt import CLASSIFY_PROMPT
from src.rag.history import format_history
from src.graph.nodes.generator_node import generator_node
from src.graph.nodes.citation_node import citation_node
from src.graph.nodes.summary_node import is_summary_request, summary_node
from src.graph.nodes.precedent_query_node import is_precedent_lookup, precedent_query_node
from src.graph.nodes.precedent_results_node import precedent_results_node
from functools import lru_cache
from time import perf_counter
import logging
from src.graph.timing import timed


# 세션에 캐시된 이전 그래프를 교체할 때 올립니다.
WORKFLOW_VERSION = 3

def route_start(state: State) -> str:
    if is_precedent_lookup(state["origin_query"]):
        return "mcp"
    llm = create_llm()
    history = format_history(state.get("messages"), max_turns=2)
    user_content = (
        f"[이전 대화]\n{history}\n\n[현재 질문]\n{state['origin_query']}"
        if history else state["origin_query"]
    )
    message = [("system", CLASSIFY_PROMPT), ("user", user_content)]
    started = perf_counter()
    response = llm.invoke(message)
    logging.getLogger("apps.chat.stages").info("chat stage=classify elapsed_seconds=%.2f", perf_counter() - started)
    label = str(response.content).strip().lower()
    return label if label in {"general", "rag", "mcp", "out_of_scope"} else "rag"

def route_image(state: State) -> str:
    if state.get("images"):
        return "image"
    if is_summary_request(state["origin_query"]):
        return "summary"
    return route_start(state)


def route_after_mcp(state: State) -> str:
    return "results" if is_precedent_lookup(state["origin_query"]) else "generator"

@lru_cache(maxsize=1)
def build_workflow():
    builder = StateGraph(State)

    builder.add_node("image", timed(image_node, "image"))
    builder.add_node("general", timed(general_node, "general"))
    builder.add_node("rag", build_rag_graph())
    builder.add_node("mcp", build_mcp_graph())
    builder.add_node("generator", timed(generator_node, "generator"))
    builder.add_node("citation", timed(citation_node, "citation"))
    builder.add_node("summary", timed(summary_node, "summary"))
    builder.add_node("precedent_query", precedent_query_node)
    builder.add_node("precedent_results", precedent_results_node)

    builder.add_conditional_edges(START , route_image ,
        {"image": "image", "summary": "summary", "general": "general", "out_of_scope": "general", "rag": "rag", "mcp": "precedent_query"})
    
    builder.add_conditional_edges("image", route_start,
        {"general": "general", "out_of_scope" : "general", "rag": "rag", "mcp" : "precedent_query"})
    
    builder.add_edge("general", END)
    builder.add_edge("summary", END)
    builder.add_edge("rag", "citation")
    builder.add_edge("precedent_query", "mcp")
    builder.add_conditional_edges("mcp", route_after_mcp, {"results": "precedent_results", "generator": "generator"})
    builder.add_edge("precedent_results", END)
    builder.add_edge("generator", "citation")
    builder.add_edge("citation", END)

    # 각 요청은 세션 DB에서 복원한 messages로 시작합니다.
    # 이전 요청의 검색 후보·검증 상태가 다음 요청에 섞이지 않습니다.
    return builder.compile()

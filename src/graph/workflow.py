from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
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

# 세션에 캐시된 이전 그래프를 교체할 때 올립니다.
WORKFLOW_VERSION = 3

def route_start(state: State) -> str:
    llm = create_llm()
    history = format_history(state.get("messages"), max_turns=2)
    user_content = (
        f"[이전 대화]\n{history}\n\n[현재 질문]\n{state['origin_query']}"
        if history else state["origin_query"]
    )
    message = [("system", CLASSIFY_PROMPT), ("user", user_content)]
    response = llm.invoke(message)
    label = str(response.content).strip().lower()
    return label if label in {"general", "rag", "mcp", "out_of_scope"} else "rag"

def route_image(state: State) -> str:
    if state.get("images"):
        return "image"
    return route_start(state)

def build_workflow():
    builder = StateGraph(State)

    builder.add_node("image", image_node)
    builder.add_node("general", general_node)
    builder.add_node("rag", build_rag_graph())
    builder.add_node("mcp", build_mcp_graph())
    builder.add_node("generator", generator_node)
    builder.add_node("citation", citation_node)

    builder.add_conditional_edges(START , route_image ,
        {"image": "image", "general": "general", "out_of_scope": "general", "rag": "rag", "mcp": "mcp"})
    
    builder.add_conditional_edges("image", route_start,
        {"general": "general", "out_of_scope" : "general", "rag": "rag", "mcp" : "mcp"})
    
    builder.add_edge("general", END)
    builder.add_edge("rag", "citation")
    builder.add_edge("mcp", "generator")
    builder.add_edge("generator", "citation")
    builder.add_edge("citation", END)

    return builder.compile(checkpointer=InMemorySaver())

from src.graph.state import State
from src.rag.generator import generator
from langchain_core.messages import HumanMessage, AIMessage
# 노드 입니다.

# state에 따라서 노드의 상태를 정의합니다.

def generator_node(state : State):
    # generator.py에서 정의한 함수의 리턴값만 받아서 다룰 예정
    result = generator(state)
    result["messages"] = [
        HumanMessage(content=state["messages_query"] if False else state["origin_query"]),
        AIMessage(content=result["answer"]),
    ]
    return result
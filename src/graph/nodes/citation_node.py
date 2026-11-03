"""답변의 주장별 출처 연결을 검사하는 그래프 노드."""

from src.graph.state import State
from src.rag.citation import citation


def citation_node(state: State):
    return citation(state)

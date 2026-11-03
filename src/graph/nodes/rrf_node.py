from src.graph.state import State
from src.rag.rrf import rrf


def rrf_node(state: State):
    response = rrf(state)
    # print(response)
    return response
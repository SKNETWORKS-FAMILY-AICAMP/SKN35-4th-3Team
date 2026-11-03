from src.graph.state import State
from src.const.model import create_llm
from src.const.prompt.general_prompt import GENERAL_PROMPT
from langchain_core.messages import SystemMessage , HumanMessage

def general_node(state : State):
    print("일반 진입")
    question = HumanMessage(content=state["origin_query"])

    response = create_llm(temperature=0.3).invoke([
        SystemMessage(content=GENERAL_PROMPT),
        *state.get("messages" , []),
        question
    ])

    return {"messages": [question, response] , "answer" : str(response.content)}

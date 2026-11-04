import json
from src.const.prompt.query_planner_prompt import QUERY_PLANNER_PROMPT

def query_planner(
        question: str,
        llm,
        history:str = "",
) -> dict :
    """질문의 사실관계와 해결 목적을 분석하고 검색 계획을 수립한다."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("질문은 비어 있지 않은 문자열이어야 합니다.")

    if not isinstance(history, str):
        raise TypeError("history는 문자열이어야 합니다.")

    origin_query = question.strip()

    human_prompt = (
        f"[이전 대화]\n{history.strip()}\n\n[현재 질문]\n{origin_query}"
        if history.strip() else origin_query
    )

    result = {
        "origin_query": origin_query,
        "facts": [],
        "conditions": [],
        "intents": [],
        "needs_rewrite": False,
        "needs_expansion": False,
    }

    try:
        response = llm.invoke([
            ("system", QUERY_PLANNER_PROMPT),
            ("human", human_prompt),
        ])

        print("LLM 원본 응답:", response.content)

        data = json.loads(response.content.strip())

        if not isinstance(data, dict):
            raise TypeError("Planner 응답은 JSON 객체여야 합니다.")

        for key in ("facts", "conditions", "intents"):
            value = data.get(key)

            if not isinstance(value, list):
                raise TypeError(f"{key}는 리스트여야 합니다.")

            if not all(isinstance(item, str) for item in value):
                raise TypeError(f"{key}의 모든 항목은 문자열이어야 합니다.")

        for key in ("needs_rewrite", "needs_expansion"):
            if not isinstance(data.get(key), bool):
                raise TypeError(f"{key}는 불리언이어야 합니다.")

        result.update({
            "facts": data["facts"],
            "conditions": data["conditions"],
            "intents": data["intents"],
            "needs_rewrite": data["needs_rewrite"],
            "needs_expansion": data["needs_expansion"],
        })




    except Exception as e:
        print(f"Query Planner 오류: {e}")

    return result


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    load_dotenv()
    llm = ChatOpenAI(model=os.environ["OPENAI_MODEL"], temperature=0)

    result = query_planner(
        "2023년 8월에 전세 계약을 맺고 보증금 2억 원을 냈어요. 계약 기간은 2년이었고, 만료되기 두 달 전에 집주인한테 카톡으로 나가겠다고 했는데 답장이 없었어요. 그런데 계약이 끝난 뒤에도 보증금을 안 돌려주고, 집주인은 새 세입자가 들어와야 돈을 줄 수 있다고 해요. 알고 보니 집이 경매에 넘어갔다고 하는데 정확히 언제 경매가 시작됐는지는 모르겠어요. 저는 아직 그 집에 살고 있고 전입신고랑 확정일자는 받아둔 상태예요. 이런 상황에서 계약이 끝난 게 맞는지, 보증금을 돌려받으려면 어떤 절차를 밟아야 하는지, 경매에서 제 보증금을 우선적으로 받을 수 있는지도 알고 싶어요.",
        llm
    )

    print(result)

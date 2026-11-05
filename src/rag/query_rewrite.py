# planner 결과를 json 문자열로 변환
import json
from src.const.prompt.query_rewriter_prompt import QUERY_REWRITE_PROMPT

def query_rewrite(
        question: str,
        llm,
        planner_result: dict | None = None,
        history: str = "",
        ) -> dict[str, str] :

    """ Planner 분석 결과를 참고하여 검색용 질의 하나를 재작성한다."""

    if not isinstance(question, str) or not question.strip() :
        raise ValueError('질문은 비어있지 않은 문자열이어야 합니다.')
    
    if not isinstance(history, str) :
        raise TypeError('history는 문자열이어야 합니다.')

    if planner_result is not None and not isinstance(planner_result, dict) :
        raise TypeError("planner_result는 딕셔너리여야 합니다.")

    # 원본 질문
    origin_query = question.strip()

    planner_data = {
        "facts": (planner_result or {}).get("facts", [] ),
        "conditions": (planner_result or {}).get("conditions", [] ),
        "intents" : (planner_result or {}).get("intents", [] ),
    }

    human_prompt = f"""
[원본 질문]
{origin_query}

[Planner 분석 결과]
{json.dumps(planner_data, ensure_ascii=False, indent=4)}

[이전 대화]
{history.strip() if history.strip() else "없음"}
"""

    try:
        response = llm.invoke([
            ("system", QUERY_REWRITE_PROMPT),
            ("human", human_prompt),
        ])

        rewritten_query = response.content.strip()

        prefixes = (
            "검색 질의:",
            "검색 질의문:",
            "다음 상황에 대한 법령 검색용 질의문:",
        )

        for prefix in prefixes:
            if rewritten_query.startswith(prefix):
                rewritten_query = rewritten_query[len(prefix):].strip()

        rewritten_query = rewritten_query or origin_query


    except Exception as e:
        print(f"Query Rewrite 오류: {e}")
        rewritten_query = origin_query

    return {
        "origin_query": origin_query,
        "rewritten_query": rewritten_query,
    }
    


if __name__ == '__main__':
    import os
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    from src.rag.query_planner import query_planner

    load_dotenv()
    llm = ChatOpenAI(model=os.environ["OPENAI_MODEL"], temperature=0)

    question = "회사에서 3년 4개월 일했는데 지난주에 갑자기 그만 나오라고 했어요. 해고 통지서는 못 받았고 마지막 두 달치 월급이랑 퇴직금도 아직 못 받았어요. 회사에서는 제가 자발적으로 퇴사한 거라고 주장하는데 저는 그런 적 없어요. 해고를 다툴 수 있는지, 밀린 돈은 어떻게 받을 수 있는지, 어디에 신고해야 하는지 각각 알려주세요."

    planner_result = query_planner(question, llm)
    result = query_rewrite(
        question = question,
        llm = llm,
        planner_result=planner_result
    )

    print(result)
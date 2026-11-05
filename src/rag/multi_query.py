import json
from src.const.prompt.multi_query_prompt import MULTI_QUERY_PROMPT

def multi_query(
    question: str,
    llm,
    rewritten_query: str | None = None,
    planner_result : dict | None = None,
    max_queries: int = 3,
) -> dict :
    """원본 질문과 재작성 질의를 기반으로 검색 질의를 가변적으로 생성한다."""

    if not isinstance(question,str) or not question.strip() :
        raise ValueError("질문은 비어있지않은 문자열이어야 합니다.")

    if rewritten_query is not None and not isinstance(rewritten_query, str) :
        raise TypeError("rewritten_query는 문자열 또는 None이어야 합니다.")

    if planner_result is not None and not isinstance(planner_result, dict) :
        raise TypeError("planner_result는 딕셔너리여야 합니다.")

    if (
        not isinstance(max_queries, int)
        or isinstance(max_queries, bool)
        or max_queries < 1
    ) :
        raise  ValueError("max_queries는 1 이상의 정수여야 합니다.")

    # 원본 질문
    origin_query = question.strip()

    # rewirte가 없으면 원본 질문 사용
    rewritten_query = (rewritten_query or "").strip()

    # Planner 결과
    planner_result= planner_result or {}

    planner_data = {
        "facts": planner_result.get("facts", []),
        "conditions": planner_result.get("conditions", []),
        "intents": planner_result.get("intents", []),
    }

    # LLM 입력 구성
    human_prompt = f"""
[원본 질문]
{origin_query}

[재작성 질의]
{rewritten_query if rewritten_query else "없음"}

[Planner 분석 결과]
{json.dumps(planner_data, ensure_ascii=False, indent=4)}

[max_queries]
{max_queries}
"""


    try:
        response = llm.invoke([
            ("system", MULTI_QUERY_PROMPT),
            ("human", human_prompt),
        ])

        data = json.loads(response.content.strip())

        if not isinstance(data, dict):
            raise TypeError("Multi-Query 응답은 JSON 객체여야 합니다.")

        queries = data.get("queries")

        if not isinstance(queries, list):
            raise TypeError("queries는 리스트여야 합니다.")

        if not all(isinstance(q, str) for q in queries):
            raise TypeError("queries의 모든 항목은 문자열이어야 합니다.")

        # 공백 제거 및 중복 제거
        queries = list(dict.fromkeys(
            q.strip()
            for q in queries
            if q.strip()
        ))

        # 최대 개수 제한
        queries = queries[:max_queries]

        # 생성된 질의가 없으면 오류 발생
        if not queries:
            raise ValueError("Multi-Query 생성 결과가 비어 있습니다.")

    except Exception as e:
        raise RuntimeError(f"Multi-Query 실행 실패: {e}") from e

    return {
        "origin_query" : origin_query,
        "queries" : queries,
    }




if __name__ == "__main__":

    import os
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    from src.rag.query_planner import query_planner
    from src.rag.query_rewrite import query_rewrite

    load_dotenv()

    llm = ChatOpenAI(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
    )

    question = (
        "회사에서 3년 4개월 일했는데 지난주에 갑자기 "
        "그만 나오라고 했어요. 해고 통지서는 못 받았고 "
        "마지막 두 달치 월급이랑 퇴직금도 아직 못 받았어요. "
        "회사에서는 제가 자발적으로 퇴사한 거라고 주장하는데 "
        "저는 그런 적 없어요. 해고를 다툴 수 있는지, "
        "밀린 돈은 어떻게 받을 수 있는지, "
        "어디에 신고해야 하는지 각각 알려주세요."
    )

    # 1. Planner 실행
    planner_result = query_planner(
        question=question,
        llm=llm,
    )

    # 2. Rewrite 조건부 실행
    if planner_result["needs_rewrite"]:

        rewrite_result = query_rewrite(
            question=question,
            llm=llm,
            planner_result=planner_result,
        )

        rewritten_query = rewrite_result["rewritten_query"]

    else:
        rewritten_query = ""

    # 3. Multi-Query 조건부 실행
    if planner_result["needs_expansion"]:

        result = multi_query(
            question=question,
            llm=llm,
            rewritten_query=rewritten_query,
            planner_result=planner_result,
            max_queries=3,
        )

    else:
        result = {
            "origin_query": question,
            "queries": [rewritten_query or question],
        }

    print("\nMulti-Query 결과:")
    print(json.dumps(result, ensure_ascii=False, indent=4))
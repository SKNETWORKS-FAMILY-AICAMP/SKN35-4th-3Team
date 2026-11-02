import re

def multi_query(
        question: str,
        llm, 
        num_expansions: int = 2,
    ) -> list[str] :
    

    # 입력 검증
    if not isinstance(question, str) or not question.strip() :
        raise ValueError('질문은 비어있지 않고 문자열이어야 합니다.')

    if type(num_expansions) is not int or not 0 <= num_expansions <= 3 :
        raise ValueError('확장 개수는 0~3 정수여야 합니다.')

    question = question.strip()

    # 확장이 필요 없으면 LLM 호출 없이 반환
    if num_expansions == 0:
        return [question]
    

    response = llm.invoke([
        ('system',
         f"한국 법령 검색을 위한 검색 질의 {num_expansions}개를 생성하라."
        "첫 번째는 핵심 법률 용어 위주의 짧은 키워드형으로 작성하라. "
        "두 번째가 필요하면 입력의 핵심 쟁점을 하나만 묻는 짧은 자연어 질문으로 작성하라. "
        "세 번째가 필요하면 같은 쟁점을 다른 검색 용어로 표현하라. "
        "요청한 개수만 출력하라. "
        "모든 질의가 단순히 동의어만 바꾼 문장이 되지 않도록 하라. "
        "단순히 어순이나 조사만 바꾼 질의를 반복하지 마라. "
        "핵심 사실관계, 부정표현, 날짜, 금액, 당사자 관계를 유지하라. "
        "입력에 없는 사실이나 법적 판단, 조문 번호를 추가하지 마라. "
        "번호, 글머리표, 설명 없이 한 줄에 질의 하나씩 출력하라."
        "각 질의는 하나의 핵심 쟁점에 집중하고 짧게 작성하라. "
        "입력에서 묻지 않은 이자, 손해배상, 절차 등 별도 쟁점을 추가하지 마라. "
        "자연어 질문은 하나의 핵심 질문으로 작성하라. "
        "여러 질문을 나열하거나 '구제의 범위'처럼 포괄적인 표현을 덧붙이지 마라. "
        "입력에 없는 특정 법률명을 추측하여 추가하지 마라. "
        "자연어 질문은 하나의 쟁점만 묻는 짧은 문장으로 작성하라. "
         ),
         ('human', question)
    ])

    # 전달받은 리라이팅 질문도 검색에 포함
    queries = [question]
    query_keys = {" ".join(question.split()).casefold()}
    
    for line in response.content.splitlines() :

        # 번호나 불릿이 붙어 있어도 제거
        query = re.sub(
            r"^\s*(?:[-*•]|\d+[.)])\s*",
            "",
            line,
        ).strip()

        # 연속 공백 정리
        query = " ".join(query.split() )

        # 영문 대소문자 차이를 무시하고 중복 확인
        key = query.casefold()

        if query and key not in query_keys :
            queries.append(query)
            query_keys.add(key)

        if len(queries) >= num_expansions +1 :
            break

    return queries



if __name__ == '__main__' :
    import os
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    from src.rag.query_rewrite import query_rewrite

    load_dotenv()
    llm = ChatOpenAI(model=os.environ["OPENAI_MODEL"])

    question = "집주인이 계약 끝났는데 보증금을 안 줘요."
    rewritten = query_rewrite(question, llm)
    
    # 리라이팅 질문을 기준으로 확장
    expanded_queries = multi_query(rewritten, llm)

    # 원본을 추가하고 순서를 유지하면서 중복 제거
    queries = list(dict.fromkeys([question, *expanded_queries]))

    print("원본질문 :", question)
    print("리라이팅 질문 :", rewritten)

    for index, query in enumerate(expanded_queries[1:], start=1):
        print(f"확장 질문 {index} :", query)

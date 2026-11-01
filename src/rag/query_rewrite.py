import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


load_dotenv()
llm = ChatOpenAI(model=os.environ["OPENAI_MODEL"])

def query_rewrite(question: str, llm) -> str :
    question = question.strip()
    if not question :
        raise ValueError('질문을 입력해 주세요.')
    
    response = llm.invoke([
        ('system',
         '사용자 질문을 한국 법령 검색에 적합한 질의 하나로 재작성하라.'
        '핵심 사실관계, 부정표현, 날짜를 유지하라.'
        '모르는 법령이나 조문 번호를 추측하지 마라.'
        '답변이나 설명 없이 검색 질의만 출력하라.'),
        ('human', question),
    ])
    
    return response.content.strip() or question

if __name__ == '__main__' :
    print(query_rewrite("집주인이 계약 끝났는데 보증금을 안 줘요.", llm))
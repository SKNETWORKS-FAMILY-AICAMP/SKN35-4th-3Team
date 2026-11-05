# 판례 유사도 검색 MCP 서브그래프

사용자 질문을 3차 프로젝트의 기존 Qdrant OpenAI 컬렉션에서 검색합니다.
`document_type=precedent`인 판례 후보만 가져와 Qdrant 유사도순으로 반환합니다.
Qdrant 결과가 최종 요청 수보다 부족하면 국가법령정보센터 공식 API 결과로
부족한 수량을 보완합니다. 특정 기준 판례는 Qdrant에서 먼저 조회하고, 없을
때 공식 API로 조회한 뒤 후보 통합이 끝난 마지막 단계에서 제외합니다.
기존 Qdrant 데이터의 적재·수정·삭제는 수행하지 않습니다.

이 서브그래프는 판례만 담당합니다. 법령·가이드 PDF·공식기관 안내·절차·
소비자분쟁해결기준 별표 PDF는 RAG 영역이며, 소비자분쟁조정 사례·표준약관·
행정규칙·고시·산정기준은 MCP 입력과 검색 대상에서 제외합니다.

## 입력

```python
{
    "user_query": "임대차 보증금 반환과 관련된 판례를 찾아줘",
    "mcp_top_n": 5,
    "mcp_candidate_limit": 20,
}
```

`user_query`만 필수입니다. 질문 임베딩에는 3차 컬렉션과 동일한
`text-embedding-3-small` 1536차원 모델을 사용합니다.

## 출력

- `mcp_similar_precedents`: Qdrant 우선·공식 API 보완 유사 판례 Top-N
- `mcp_excluded_precedents`: 검색에서 제외된 판례
- `similarity_score`: Qdrant 또는 공식 API 출처별 유사도 원본
- `similarity_percent`: 화면 표시용 유사도 퍼센트 문자열
- `similarity_rank`: Qdrant 우선·공식 API 보완 기준 최종 순위
- `source_url`: 공식 판례 출처

`similarity_percent`는 출처별 유사도 점수를 읽기 쉽게 변환한 표시값이며 정답
확률이나 법률적 신뢰 확률이 아닙니다. Qdrant Cosine과 공식 API 텍스트 유사도는
직접 비교하지 않고, Qdrant 순위를 우선한 뒤 부족한 수량만 공식 API로 채웁니다.

## MCP Tool

- `find_similar_precedents`: 자연어 질문으로 유사 판례 Top-N 검색
- `compare_precedent_similarity`: 특정 기준 판례와 유사한 판례 Top-N 비교

공식 API는 기준 판례가 Qdrant에 없거나 Qdrant 유사 판례가 요청 수보다 부족할
때만 fallback으로 사용하며, 별도 MCP Tool로 노출하지 않습니다.

## 실행 경계

이 패키지는 Django URL, View, 공용 workflow를 수정하지 않습니다. 팀 통합
단계에서 `build_mcp_graph()`를 공용 workflow에 연결하거나
`MCPToolService`를 Django 서비스에서 호출합니다.

`mcp_reference_candidate`가 입력되면 기준 판례 비교 노드로 분기하고, 없으면
기존 자유질문 판례 검색 노드를 실행합니다.

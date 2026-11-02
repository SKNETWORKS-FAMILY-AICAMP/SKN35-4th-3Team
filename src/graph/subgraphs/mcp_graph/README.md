# 판례 유사도·검증 MCP 서브그래프

사용자 질문을 3차 프로젝트의 기존 Qdrant OpenAI 컬렉션에서 검색합니다.
`document_type=precedent`인 판례 후보만 가져온 뒤 국가법령정보센터 공식 API로
실제 존재와 식별정보를 검증하고, 검증된 후보를 Qdrant 유사도순으로 반환합니다.
기존 Qdrant 데이터의 적재·수정·삭제는 수행하지 않습니다.

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

- `mcp_similar_precedents`: 공식 API에서 확인한 유사 판례 Top-N
- `mcp_excluded_precedents`: 공식 상세 조회에 실패한 판례
- `similarity_score`: Qdrant Cosine 유사도 원본
- `similarity_percent`: 화면 표시용 유사도 퍼센트 문자열
- `verified_rank`: 공식 API 검증 후 Qdrant 유사도 기준 순위
- `source_url`: 공식 판례 출처

`similarity_percent`는 Qdrant 점수를 읽기 쉽게 변환한 표시값이며 정답 확률이나
법률적 신뢰 확률이 아닙니다. 계산과 정렬에는 변환 전 원본 점수를 사용합니다.

## MCP Tool

- `find_similar_precedents`: 질문으로 공식 판례를 검색하고 Top-N 유사도 반환
- `compare_precedent_similarity`: 기준 판례와 유사한 공식 판례 Top-N 비교
- `verify_precedent`: 사용자가 제시한 판례 한 건을 공식 판례와 대조
- `verify_precedent_candidates`: 여러 판례를 일괄 대조
- `get_precedent_detail`: 공식 판례 상세 조회
- `health_check`: 공식 API 연결 확인

## 실행 경계

이 패키지는 Django URL, View, 공용 workflow를 수정하지 않습니다. 팀 통합
단계에서 `build_mcp_graph()`를 공용 workflow에 연결하거나
`MCPToolService`를 Django 서비스에서 호출합니다.

`mcp_reference_candidate`가 입력되면 기준 판례 비교 노드로 분기하고, 없으면
기존 자유질문 판례 검색 노드를 실행합니다.

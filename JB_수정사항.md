# JB 수정사항

> 기준: 현재 `jb` 작업 트리의 줄 번호입니다. 이후 다른 작업과 머지하거나 파일을
> 추가 수정하면 줄 번호가 이동할 수 있습니다.

## 1. 작업 목적

3차 프로젝트의 기존 Qdrant 환경과 `team_documents_openai_dev` 컬렉션을
읽기 전용으로 재사용합니다. 사용자가 제시한 기준 판례를 공식 API로 확인한 뒤,
기준 판례 내용을 Qdrant에서 검색하여 유사 판례 Top-N과 화면 표시용 유사도
퍼센트를 반환합니다.

기존 Qdrant 데이터의 적재·수정·삭제는 수행하지 않습니다.

## 2. 핵심 기능 수정

### `src/tools/qdrant_precedent.py`

- 35~42행: Qdrant 판례 검색 Protocol에 `exclude_precedent_id` 옵션 추가
- 68~182행: 3차 Qdrant 판례 검색, 판례 단위 중복 제거 및 기준 판례 제외
- 185~235행: 3차 OpenAI 임베딩 규격과 Qdrant Client 생성
- 현재 규격: `text-embedding-3-small`, 1536차원, Cosine, Named Vector 미사용
- 검색 필터: `document_type=precedent`

### `src/tools/precedent_mcp.py`

- 30~47행: 기준 판례의 사건명·판시사항·판결요지·참조조문·본문 일부를 검색문으로 조합
- 180~241행: 기존 자유질문 Qdrant 판례 검색 유지
- 243~319행: `compare_precedent_similarity()` 추가
  - 기준 판례 공식 검증
  - 기준 판례 검색문 생성
  - Qdrant 유사 판례 검색
  - 기준 판례 자신 제외
  - 검색 후보 공식 API 재검증
  - Qdrant 유사도순 Top-N 반환
- 322~328행: 검색기를 선택적으로 주입할 수 있도록 서비스 팩토리 확장

### `src/tools/mcp_runtime.py`

- 64~75행: 기존 `find_similar_precedents` Tool 유지
- 78~89행: `compare_precedent_similarity` MCP Tool 등록
- 94~99행: 팀 기본 MCP 서버에 3차 Qdrant 판례 검색기 연결

## 3. 유사도 퍼센트 표시

### `src/tools/precedent_verification.py`

- 63~81행: `format_similarity_percent()` 추가
- 205행: 외부 검색 점수의 `similarity_percent` 출력
- 291행, 555행: MCP 텍스트 유사도의 퍼센트 출력

예시:

```text
원본 점수: 0.876
표시 값: 87.6%
```

퍼센트는 화면 표시용 값이며 정답 확률 또는 법률적 신뢰 확률이 아닙니다.
정렬과 계산에는 변환 전 원본 점수를 사용합니다.

## 4. MCP 그래프 연결

### `src/graph/subgraphs/mcp_graph/state.py`

- 14행: `mcp_reference_candidate` 입력 추가
- 18행: `mcp_reference_precedent` 출력 추가
- 19행: `mcp_similar_precedents` 출력 유지·공유

### `src/graph/subgraphs/mcp_graph/nodes.py`

- 19행: 서비스 Protocol에 판례 비교 Tool 추가
- 107~186행: 기준 판례 비교 Tool Node 추가
- 189~201행: 자유질문 검색 Node와 기준 판례 비교 Node를 함께 생성

### `src/graph/subgraphs/mcp_graph/graph.py`

- 44~54행: 기본 서비스의 기준 판례 비교 호출 함수
- 57~60행: `mcp_reference_candidate` 존재 여부에 따른 분기
- 63~90행: 검색 Node와 비교 Node를 독립 경로로 연결

```text
mcp_reference_candidate 없음
→ mcp_find_similar_precedents

mcp_reference_candidate 있음
→ mcp_compare_precedent_similarity
```

### `src/graph/subgraphs/mcp_graph/__init__.py`

- 6행, 17행: 기준 판례 비교 Node 생성 함수를 공개 진입점에 추가

## 5. 공개 함수·환경설정·의존성

### `src/tools/__init__.py`

- 18행: 퍼센트 출력 함수 공개
- 26~28행: Qdrant 판례 검색 클래스·팩토리 공개
- 39행, 42행, 48~49행: `__all__` 등록

### `.env.example`

- 15행: 국가법령정보센터 `LAW_API_OC`
- 18~24행: Qdrant·OpenAI 검색 설정

### `pyproject.toml`

- 9~14행: LangGraph, MCP, OpenAI, Qdrant Client, Requests 의존성
- `uv.lock`: 위 의존성에 맞춰 자동 갱신

## 6. 문서·검증 관련 수정

### `src/graph/subgraphs/mcp_graph/README.md`

- 3행부터: 공식 API 단독 검색 설명을 Qdrant 후보 검색·공식 검증 흐름으로 최신화
- 36행: `compare_precedent_similarity` Tool 설명 추가
- 48~49행: 기준 판례 입력에 따른 그래프 분기 설명 추가

### `MCP_작업개요.md`

- 22행: Qdrant 판례 후보 검색 흐름 반영
- 39행: 기준 판례 비교 Tool 추가
- 79행: 그래프 기준 판례 출력 추가
- 175~176행: Qdrant 연결 및 실제 판례 필터 검색 완료 상태 반영

### `src/evaluation/test_precedent_mcp.py`

- 301행: MCP Tool 등록 기대값에 기준 판례 비교 Tool 추가
- 312행: MCP Node 기대값에 기준 판례 비교 Node 추가

## 7. 검증 결과

- 기준 판례 비교 Service 호출: 통과
- 기준 판례 비교 LangGraph 분기: 통과
- 기준 판례 자신 제외: 통과
- 유사도 `0.876` → `87.6%` 변환: 통과
- 기존 MCP 단위 테스트: 12건 통과
- Django 시스템 검사: 통과
- 3차 Qdrant 실제 판례 검색: 3건 반환 확인
- Qdrant 데이터 변경: 없음

## 8. 현재 남은 작업

- 4차 프로젝트 `.env`에 Qdrant·OpenAI·국가법령정보센터 인증값 설정
- 실제 국가법령정보센터 API까지 포함한 전체 실환경 검증
- Django 화면 담당 영역에서 `similarity_percent` 출력 연결
- 추후 판례 데이터 교체 시 기존 컬렉션의 판례 Point만 동기화

본 문서는 `26.11.02_JB_Update` 커밋의 변경 범위를 기록합니다.

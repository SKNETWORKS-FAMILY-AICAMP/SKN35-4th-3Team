# 4차 프로젝트 MCP 작업 개요

## 1. 작업 목적

사용자가 판례를 질문하거나 특정 판례를 제시하면 공식 판례 데이터와 대조하여
다음 정보를 제공합니다.

- 공식 판례의 실제 존재 여부
- 사건번호·사건명·법원·선고일 일치 여부
- 사용자 질문과 공식 판례 내용의 유사도
- 다른 유사 판례 Top-N과 순위
- 국가법령정보센터 공식 출처

MCP는 판례 검색과 검증을 담당하며 최종 자연어 답변 생성과 화면 표시는 다른
작업 영역에서 연결합니다.

## 2. 현재 처리 흐름

```text
사용자 질문
→ MCP 판례 검색 Tool 호출
→ 3차 Qdrant에서 판례 후보 검색
→ 국가법령정보센터 공식 API 상세 조회
→ 실제 존재·식별정보 검증
→ Qdrant 유사도 기준 Top-N 정렬
→ 검증 상태·유사도·공식 출처 반환
```

현재 흐름은 별도 RAG 그래프 없이 MCP만으로 실행할 수 있습니다. 유사 판례
후보와 순위는 3차 Qdrant의 OpenAI Cosine 유사도를 사용하고, 국가법령정보센터
공식 API는 판례의 실제 존재와 식별정보를 검증합니다. 퍼센트 표시는 Qdrant
원본 점수를 화면에서 읽기 쉽게 변환한 값이며 정답 확률이 아닙니다.

## 3. MCP Tool

| Tool | 역할 |
| --- | --- |
| `find_similar_precedents` | 질문과 유사한 공식 판례 Top-N 검색 |
| `compare_precedent_similarity` | 기준 판례 확인 후 유사 판례 Top-N 비교 |
| `verify_precedent` | 특정 판례 한 건의 공식 정보와 내용 대조 |
| `verify_precedent_candidates` | 여러 판례 후보 일괄 검증 |
| `get_precedent_detail` | 판례정보일련번호로 공식 상세 조회 |
| `health_check` | MCP와 공식 판례 API 연결 상태 확인 |

## 4. 3차 프로젝트와 맞춘 호출 함수

3차 프로젝트와 역할이 같은 함수는 동일한 이름을 사용합니다.

- `create_tool_service()`
- `create_mcp_server()`
- `create_team_mcp_server()`
- `create_mcp_tool_nodes()`
- `build_mcp_graph()`
- `serialize_tool_response()`
- `parse_tool_response()`

4차 프로젝트의 신규 기능명인 `find_similar_precedents()`만 별도로 사용합니다.
이전 4차 전용 호출명의 호환 별칭은 두지 않습니다.

## 5. 그래프 입출력

### 입력

```python
{
    "user_query": "임대차 보증금 반환과 관련된 판례를 찾아줘",
    "mcp_top_n": 5,
    "mcp_candidate_limit": 20,
}
```

질문 키는 팀 공용 연결을 위해 `user_query`, `rewrite_query`, `origin_query`를
지원합니다.

### 출력

- `mcp_status`: MCP 처리 상태
- `mcp_result`: 전체 MCP 응답
- `mcp_reference_precedent`: 사용자가 제시한 공식 기준 판례
- `mcp_similar_precedents`: 유사 판례 목록
- `mcp_documents`: 공용 MCP 문서 목록
- `documents`: 후속 그래프 연결용 문서 목록
- `mcp_excluded_precedents`: 상세 조회 실패 등 제외 목록
- `mcp_error`: 안전하게 변환된 오류 정보

## 6. 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `src/const/precedent.py` | 공식 API 주소·환경변수·응답 상태 상수 |
| `src/tools/precedent_api.py` | 국가법령정보센터 검색·상세 조회 클라이언트 |
| `src/tools/precedent_verification.py` | 판례 식별정보 검증·유사도·Top-N 계산 |
| `src/tools/qdrant_precedent.py` | 3차 Qdrant 판례 필터 검색·후보 정규화 |
| `src/tools/precedent_mcp.py` | MCP 업무 서비스와 JSON 응답 계약 |
| `src/tools/mcp_runtime.py` | MCP SDK Tool 등록과 서버 생성 |
| `src/graph/subgraphs/mcp_graph/nodes.py` | MCP Tool을 독립 그래프 노드로 변환 |
| `src/graph/subgraphs/mcp_graph/state.py` | MCP 서브그래프 상태 정의 |
| `src/graph/subgraphs/mcp_graph/graph.py` | 독립 MCP 서브그래프 생성 |
| `src/evaluation/test_precedent_mcp.py` | 네트워크 없는 MCP 단위 테스트 |

새 기능 폴더는 만들지 않았으며 팀이 준비한 기존 폴더 안에 파일만 추가했습니다.

## 7. 환경설정

`.env`에 국가법령정보센터 Open API 인증값이 필요합니다.

```ini
LAW_API_OC=
```

인증값은 Git에 커밋하지 않습니다. 현재 실제 공식 API 호출은 인증값 설정 후
추가 검증이 필요합니다.

## 8. Qdrant 연결 구조

기존 Qdrant의 판례 데이터와 벡터 설정을 재사용할 예정입니다.

```text
사용자 질문
→ 기존 컬렉션과 동일한 모델로 질문 임베딩
→ Qdrant에서 판례 자료만 필터 검색
→ 같은 판례의 여러 청크를 판례 단위로 결합
→ Qdrant 유사도 기준 Top-N 선정
→ 공식 판례 API로 실제 존재와 식별정보 검증
→ 유사도·검증 상태·공식 출처 반환
```

점수는 다음과 같이 구분합니다.

- `qdrant_similarity_score`: 질문과 저장 판례의 의미상 유사도 원본
- `similarity_percent`: 위 점수를 화면 표시용 퍼센트로 변환한 문자열
- `verification_status`: 공식 API 검증 상태
- 현재 문자 기반 유사도: Qdrant 미연결·장애 시 보조 점수

MCP Tool과 그래프의 호출 방식은 유지하고 `MCPToolService`에 Qdrant 검색
백엔드를 주입하는 방식으로 연결합니다.

## 9. Qdrant 연결 규격

- 컬렉션: `team_documents_openai_dev`
- 임베딩 모델·차원: `text-embedding-3-small`, 1536차원
- Named Vector: 미사용
- 거리 함수와 검색 방식
- 판례를 구분하는 Payload 필드
- `precedent_id`, `document_id`, `chunk_id`의 실제 키
- 판례 본문 필드명
- Point ID 생성 규칙
- Point가 판례 전체인지 청크 단위인지

기존 판례 중 특정 분야만 심화한다면 벡터 설정은 변경하지 않고 Payload 필터와
검색 흐름만 변경할 수 있습니다.

## 10. 데이터 갱신 원칙

데이터 적재·수정은 읽기 전용 MCP Tool과 분리합니다.

```text
공식 API 데이터 수집
→ 기존 source_hash와 비교
→ 변경된 판례만 재임베딩
→ 동일 Point ID로 Qdrant Upsert
→ 중복·누락·잔여 Point 검증
```

- 본문이나 청크가 바뀌면 Payload와 벡터를 함께 갱신합니다.
- 임베딩 모델이 바뀌면 기존 컬렉션을 덮어쓰지 않고 새 컬렉션을 사용합니다.
- 3차 컬렉션을 계속 사용하는 경우 기존 데이터 직접 덮어쓰기를 피합니다.
- 자동 삭제는 하지 않고 검증 결과를 확인한 뒤 별도로 처리합니다.

## 11. 검증 상태

- MCP 단위 테스트: 12건 통과
- Django 시스템 검사: 통과
- MCP Tool 5종 등록 확인: 완료
- Qdrant 읽기 전용 연결 코드: 완료
- 3차 Qdrant 실제 판례 필터 검색: 완료
- 4차 기본 실행 환경: 개인 `.env` 설정 필요
- 국가법령정보센터 실제 API 호출: 인증값 설정 후 검증 필요
- 공용 workflow·Django URL·화면 연결: 미진행

## 12. 머지 주의사항

- 공용 workflow, RAG, Django 앱, URL, 화면 파일은 현재 수정하지 않았습니다.
- Qdrant 연결 시 기존 검색 구현을 복사하지 말고 검색 백엔드로 주입합니다.
- 실제 Payload 구조가 확정되면 결과 정규화 함수와 필터만 수정합니다.
- `.env`, API Key, Qdrant Key는 커밋하지 않습니다.
- `.venv`, `.pytest_cache`, `__pycache__`는 자동 생성 폴더이며 Git 대상이 아닙니다.
- 현재 작업 내역은 아직 커밋·푸시하지 않은 상태입니다.

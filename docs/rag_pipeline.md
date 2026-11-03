# 데이터 전처리·청킹

인계 기준일: 2026-11-03.

소비자 환불·계약해지, 전세·월세 분쟁과 보증금 반환, 아르바이트 임금체불·해고에 필요한 자료와 공통 법령을 처리한다.
별도 판례 데이터셋은 포함하지 않는다. 안내서·법령 해설에 들어 있는 판례 인용은 남아 있을 수 있다.

## RAG 담당자 인계 요약

전처리와 청킹은 완료되어 있으며 Django 관리 명령어로도 실행할 수 있다.
임베딩 벡터 생성, 벡터 DB 적재, 서비스 검색·리랭킹, Django 상담 요청 처리 연결은 아직 수행하지 않았다.

| 항목 | 현재 결과 |
| --- | ---: |
| 원본 파일 | 125개 |
| 전처리 JSON | 125개 |
| 청킹 대상 부모 근거 | 3,694개 |
| 검색 대상에서 제외해 보관한 근거 | 845개 |
| 청크 | 4,276개 |
| 소비자 자료 청크 | 159개 |
| 안내서 청크 | 433개 |
| 법령·부록 청크 | 3,684개 |
| 청크 본문 최대 길이 | 900자 |
| 현재 검색 입력 최대 길이 | 1,069자 |

폴더는 자료 종류별로 나뉘어 있다. 세 상담 주제와 폴더가 일대일로 대응하지 않으므로
폴더 이름만으로 상담 주제를 제한하지 않는다. 길이는 토큰 수가 아닌 문자 수이며,
임베딩 모델의 토큰 제한은 적재할 때 별도로 확인한다.

### 검색에 사용할 필드

| 필드 | 사용 방법 |
| --- | --- |
| `chunk_id` | 벡터 DB 레코드 식별자. 검색 입력 변경 여부를 이것만으로 판단하지 않음 |
| `embedding_text` | 임베딩할 문자열. 제목·위치·일부 검토한 조건 문맥을 포함 |
| `text` | 원문 근거의 일부인 청크 본문. 표시·본문 대조에 사용 |
| `parent_id` | 전처리 문서 `units`의 `unit_id`와 연결 |
| `document_id` | 원본 문서 식별자 |
| `start`, `end` | 부모 근거 `text`의 문자 구간. 끝 위치는 포함하지 않음 |
| `title`, `locator` | 문서명과 조문·페이지·제목 위치 |
| `source_path`, `source_url`, `raw_sha256` | 수집 원본 경로·URL·해시. URL이 개별 첨부 파일을 가리키지 않는 경우도 있음 |
| `dates`, `effective_date` | 수집한 날짜·시행일 정보. 문서에 따라 없거나 의미가 다르므로 최신성 확인에 주의 |
| `requires_parent_evidence` | 검색 후 부모 근거 전체에서 조건·예외 확인 필요 |
| `retrieval_context`, `retrieval_context_sources` | 일부 근거에만 있는 추가 문맥과 부모 본문 내 출처 위치 |
| `supplemental_sources` | 일부 근거의 보완 출처. 수식 보완 PDF 등의 경로·URL·해시 |
| `canonical_source_id`, `canonical_source_scope`, `canonical_source_url` | 검토한 동일 버전 별표의 출처 연결. 선택 필드이며 본문 완전 동일성을 뜻하지 않음 |
| `calculation_restricted` | 설정된 자료는 청크 조각만으로 수치를 계산하지 않음. 필드가 없다고 계산 가능하다는 뜻도 아님 |

추가 문맥은 이미 `embedding_text`에 포함되어 있으므로 적재 시 다시 덧붙이지 않는다.
`processed/`의 모든 `units`를 임베딩하면 제외 대상이 다시 들어갈 수 있다.
임베딩할 청크는 `data/chunk/`에서 읽는다.

### 읽기와 부모 근거 연결 예시

아래 예시는 데이터 로딩과 연결 확인만 수행한다. 임베딩 API·벡터 DB 호출은 포함하지 않는다.

```python
from src.scripts.data_io import load_chunks, load_processed_data

chunks = list(load_chunks())
parents = {unit["unit_id"]: unit for unit in load_processed_data()}

embedding_inputs = [chunk["embedding_text"] for chunk in chunks]

# 실제 검색에서는 검색 결과에 저장한 parent_id로 같은 조회를 수행한다.
chunk = chunks[0]
parent = parents[chunk["parent_id"]]
assert chunk["text"] == parent["text"][chunk["start"]:chunk["end"]]
```

두 함수의 기본 경로는 프로젝트 루트를 기준으로 한다. `load_processed_data()`는
원본·보완 출처의 해시를 검사한다. `load_chunks()` 자체는 JSONL을 읽는 함수다.
부모 근거의 `evidence_metadata`에는 원래 추출 내용·분리한 화면 문구·보완 이력이 있으므로
벡터 DB의 단순 메타데이터에 전부 넣기보다 별도 근거 저장소에서 보관·조회할 수 있다.

### RAG 연결 순서와 갱신 기준

1. 현재 청크 4,276개를 읽고 `embedding_text`로 임베딩한다.
2. `chunk_id`와 함께 본문·부모 ID·위치·출처·필요한 선택 메타데이터를 적재한다.
3. 질문 검색과 재순위화를 구성하고, 검색된 `parent_id`로 전체 근거를 조회한다.
4. 조건·예외와 출처를 확인할 수 있도록 전체 근거를 답변 단계에 전달한다.
5. 동일 출처 중복 묶기는 아래 검증 결과를 참고해 검색 단계에서 별도로 적용한다.

전처리·청킹은 데이터 준비 단계에서 실행한다. Django 요청이 들어올 때마다 실행하지 않는다.
질문과 문서는 같은 임베딩 모델·호환되는 입력 방식을 사용해야 한다.

청크 본문·분할 구간이 바뀌면 ID가 바뀔 수 있으므로 이전 레코드의 정리도 필요하다.
제목·추가 문맥만 바뀌면 ID는 그대로여도 `embedding_text`가 달라질 수 있다.
적재 시 검색 입력의 해시나 데이터 버전을 별도로 관리해 재임베딩 여부를 판단한다.
`canonical_source_id` 같은 메타데이터만 바뀌고 검색 입력이 같다면 메타데이터 갱신으로 반영할 수 있다.

현재 공통 출처 ID는 근로기준법 시행령 별표 2의 두 근거·청크 4개에만 부여되어 있다.
전체 후보 26묶음 중 나머지 25묶음은 자동으로 묶지 않았다.
이 ID만으로 원본·청크를 삭제하지 않는다. 묶은 결과에서도 원래 부모 ID와 각 출처를 조회할 수 있어야 한다.

## 검증 결과와 인계 시 남은 사항

최근 기본 테스트 43개, Django 관리 명령어 연결 테스트 8개와 Django 기본 점검을 통과했다.
원본 재추출과 저장된 전처리 결과의
일치, 청크 재생성 일치, 원본 해시·중복 ID·본문 위치·전체 본문 포함 여부·길이 제한을 확인했다.
Django 관리 명령어도 실제 원본 125개를 읽어 별도 임시 경로에 실행했으며,
전처리 문서 125개와 청크 4,276개가 기존 결과와 모두 일치했다. 기존 데이터는 덮어쓰지 않았다.
이 검사는 처리 재현성과 무결성 검사이며, 모든 PDF 표·이미지의 정확성을 보장하지 않는다.

아래는 Django 연결 없이 수행한 오프라인 어휘 검색 실험이다. 한국어 단어·2글자 단위 BM25,
질문 표현 정규화, 순위 결합과 어휘 기반 재순위화를 사용했다.
의미 임베딩·학습된 리랭커·답변 품질을 평가한 결과는 아니다.

| 질문 집합 | 평가 질문 수 | 기존 Hit@5 | 출처 중복 묶기 Hit@5 | 기존 MRR@5 | 출처 중복 묶기 MRR@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 기존 질문 | 43 | 67.44% | 67.44% | 0.4492 | 0.4492 |
| 추가 질문 | 15 | 73.33% | 73.33% | 0.4444 | 0.4500 |
| 합계 | 58 | 68.97% | 68.97% | 0.4480 | 0.4494 |

전체 질문은 59개이며 기대 근거가 미확정인 1개는 점수에서 제외했다.
정답 기준은 지정한 문서·위치이므로 다른 유효한 안내서를 찾아도 실패로 처리될 수 있다.
개선 과정에서 사용한 개발 질문이며 독립 평가셋은 아니다.
공통 출처를 묶으면 질문 4개에서 상위 결과의 중복 점유가 줄었고, 한 질문의 정답이 4위에서 3위가 됐다.
새로 성공한 질문은 없었다. 중복 묶기 코드는 임시 평가에서만 실행했으며 서비스에는 연결하지 않았다.

평가 코드와 두 질문 파일은 커밋 `1465185`의 `src/evaluation/retrieval_check.py`,
`tests/data/retrieval_cases.json`, `tests/data/retrieval_followup_cases.json`에서 확인할 수 있다.
현재 작업 폴더에서는 삭제한 상태이며 인계 문서 작성 때문에 다시 추가하지 않았다.

실패 질문 18개는 현재 어휘 검색에서 후보 범위 밖 6개, 문서당 결과 제한 7개,
최종 5위 밖 5개로 분류됐다. 제한을 풀기만 하면 성공한다는 의미는 아니다.
RAG 연결 후 실제 임베딩·후보 선택·재순위화·중복 및 문서 다양성 정책을 같은 질문으로 비교한다.
기대 근거가 부족한 질문의 자료 보충과 대체 정답 검토는 별도로 남아 있다.

## 폴더 구조

```text
data/
├─ data_sources.json              # 원본별 제목·출처·날짜·주제 목록
├─ raw/                           # 수집 원본: 실행 중 수정하지 않음
│  ├─ consumer_sources/
│  ├─ guides/
│  ├─ laws/
│  ├─ law_appendices/
│  └─ law_history/
├─ processed/                     # 원본 문서별 전처리 JSON
│  ├─ consumer_sources/
│  ├─ guides/
│  └─ laws/
│     ├─ appendices/
│     └─ history/                 # 과거 법령: 참고용, 청킹 제외
└─ chunk/                         # 자료 종류별 청크 JSONL
   ├─ consumer_sources/chunks.jsonl
   ├─ guides/chunks.jsonl
   └─ laws/chunks.jsonl
```

| 파일 | 역할 |
| --- | --- |
| `src/scripts/preprocess_data.py` | 원본 XML·HTML·HWPX·PDF 전처리 |
| `src/scripts/chunk_data.py` | 청크 분할과 크기·위치·누락 검사 |
| `src/scripts/data_io.py` | 자료 읽기·저장, 원본과 보완 출처의 해시 확인 |
| `src/scripts/pdf_tables.py` | 검토한 PDF 표의 열·페이지 연결과 환불 수식 보완 |
| `data/data_sources.json` | 원본별 제목·출처 URL·날짜·주제 설정 |
| `pyproject.toml`의 `ingestion` 그룹 | 원본 추출에 필요한 추가 의존성 |
| `tests/test_preprocessing.py` | 전처리와 출처 보존 테스트 |
| `tests/test_chunking.py` | 청크 크기·위치·본문 포함 여부 테스트 |

## 실행

프로젝트 루트에서 가상환경과 추출 의존성을 준비한다.

```powershell
uv sync --group ingestion
```

원본 전처리 (`raw/` → `processed/`):

```powershell
.\.venv\Scripts\python.exe manage.py preprocess_data
```

전처리 결과 청킹 (`processed/` → `chunk/`):

```powershell
.\.venv\Scripts\python.exe manage.py chunk_data --size 900 --overlap 100
```

각 명령은 해당 단계의 결과를 갱신한다. 원본 처리에 실패하면 전처리 결과를 교체하지 않고 보고서에 남긴다. 청킹 시 원본과 보완 출처의 해시가 달라지면 중단한다. 청크 크기는 본문 문자 수 기준이다.

관리 명령어는 `apps/chat/management/commands/`에 있으며 기존 `src/scripts/` 함수를 호출한다.
`config/settings.py`의 `DATA_RAW_DIR`, `DATA_PROCESSED_DIR`, `DATA_CHUNK_DIR`,
`DATA_REPORT_DIR`로 경로를 지정한다. 기본 경로는 위 폴더 구조와 같다.
입력·출력 경로가 서로 겹치거나 출력이 프로젝트의 `data/raw/`와 겹치면 실행을 중단한다.
전처리 진행 상황과 완료 건수는 터미널에 출력하며, 실패는 `CommandError`로 알린다.
두 단계는 순서대로 수동 실행한다. 서버 시작이나 상담 요청에서 자동 실행하지 않는다.

Django를 통하지 않는 기존 실행 방법도 유지한다.

```powershell
.\.venv\Scripts\python.exe -m src.scripts.preprocess_data
.\.venv\Scripts\python.exe -m src.scripts.chunk_data --size 900 --overlap 100
```

기본 동작 점검:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe manage.py test apps.chat -v 2
.\.venv\Scripts\python.exe manage.py check
```

## Python에서 호출

```python
from src.scripts.preprocess_data import preprocess_data
from src.scripts.chunk_data import chunk_data

preprocessing_result = preprocess_data(
    raw_dir="data/raw",
    processed_dir="data/processed",
)
chunking_result = chunk_data(
    processed_dir="data/processed",
    chunk_dir="data/chunk",
    raw_dir="data/raw",
    chunk_size=900,
    overlap=100,
)
```

경로는 문자열 또는 `Path`로 전달한다. 별도 원본 경로를 사용하면 두 함수에 같은 `raw_dir`를 전달한다. `report_dir`로 보고서 저장 위치를 지정할 수 있다. 함수는 결과 딕셔너리를 반환하며 실패하거나 경로·설정이 잘못되면 예외를 발생시킨다. Django 요청 처리와의 연결은 별도 작업이다.

## 전처리 기준과 출력

전처리 JSON 하나가 원본 문서 하나에 대응한다. `units`에 조문·본문·표와 검토 상태를, PDF의 `pages`에 페이지별 추출 결과를 저장한다. 청킹 대상은 `embedding_candidate: true`인 근거다. 과거 자료, 삭제 안내, 제목만 남은 조문, 추가 검토가 필요한 자료는 제외 사유와 함께 보관한다. 길이가 짧아도 본문이 완전한 조문은 유지한다.

소비자원 HTML은 본문과 표를 유지한다. 게시판 조회수·수정일은 `page_metadata`에, 접수 안내의 지정된 팝업은 `excluded_popups`에 따로 저장한다. 검토한 인터넷 쇼핑 PDF의 청약철회 방해문구 예시에는 이전 페이지의 설명을 붙이고, 실제 물품반환 비용 안내와 구분한다. 원래 추출 내용과 문맥 위치는 메타데이터로 보존한다.

검토한 법령 부록 PDF의 네 행은 공통기준과 함께 저장한다. 지정한 파일명·해시와 표 검사 결과가 일치할 때만 적용하며, 불완전한 기존 문장 조각은 청킹에서 제외한다. 다른 PDF는 기존 추출 방식을 사용한다.

청크의 `parent_id`는 전처리 문서의 `unit_id`와 연결된다. `start/end`는 부모 본문의 문자 위치이며 청크 본문은 해당 구간과 일치한다. 조건과 예외는 부모 근거 전체에서 확인해야 한다. `embedding_text`는 제목·위치를 붙인 문자열이며 임베딩 벡터 생성은 포함하지 않는다.

소비자원 `board_insert` 게시판의 등록일·조회수 행, 이전글·다음글, 화면 설명용 캡션과
확인한 게시물 관리 하단 영역은 본문에서 분리한다. 분리한 내용은
`evidence_metadata.excluded_screen_elements`에 위치·사유와 함께 보관하며,
등록일·조회수 값은 `page_metadata`에도 저장한다. 본문·첨부파일·본문 내부 표는 유지한다.

체육시설업 환불표의 청크에는 겹치는 원문 구간의 사업자·소비자 책임, 이용개시일 전·후,
사은품 반환·참고 구간과 표의 열 제목을 `retrieval_context`로 표시한다.
이 문맥은 `embedding_text`에만 덧붙이고 청크 본문·위치·ID는 유지한다.
여러 조건이 들어 있는 청크는 포함 구간별로 표시하며, 문맥 출처의 부모 본문 위치는
`retrieval_context_sources`에 저장한다. 기간·횟수별 계산식을 새로 해석하거나 계산하지 않는다.
추가 문맥은 최대 700자로 검사하며, 청크 본문 최대 900자 제한은 그대로 유지한다.
청크 ID가 같아도 `embedding_text`가 달라진 경우에는 임베딩을 갱신해야 한다.

긴 조문 구조 분할은 검토한 전자상거래법 제17·18조와 주택임대차보호법 제3조의3에 우선 적용한다.
900자 안에 들어오는 항은 호와 단서를 포함해 함께 유지하고, 더 긴 항만 호·문장 경계로 나눈다.
이때 중복은 설정한 100자 이내에서 완전한 항·호를 반복할 수 있을 때만 적용한다.
긴 항·호의 중간부터 시작하는 청크에는 원문의 도입부와 위치를 검색 문맥으로 연결한다.
도입부 자체가 문맥 길이 제한을 넘으면 조건문을 잘라 넣지 않고 소속 번호와 부모 근거 확인 안내를 표시한다.
다른 조문을 자동으로 합치거나 참조 조문의 법률 내용을 추정하지 않는다.
구조 분할 대상 청크는 `split_basis: legal_structure_with_parent_evidence`로 구분한다.

목차를 확인할 수 있는 PDF 안내서는 목차 제목과 본문의 번호·제목이 일치할 때만
페이지 제목 문맥을 갱신한다. 판결 날짜나 본문의 번호 목록은 제목으로 사용하지 않는다.
상위 절은 같은 페이지 본문에서 확인한 경우에만 함께 표시하고, 여러 절이 있으면 `|`로 구분한다.
제목이 없는 다음 페이지에는 앞선 절의 문맥을 이어간다. PDF 본문과 표는 변경하지 않는다.

근로기준법 시행령 `003058_270551_20251023`의 별표 2는 XML과 단일 페이지 PDF에
함께 수록되어 있다. 검토한 두 원본 해시가 일치할 때만 두 근거에 같은
`canonical_source_id`와 공식 PDF URL을 기록하고 청크에도 전달한다.
`canonical_source_scope: same_version_appendix_document`는 동일 버전·별표라는 뜻이다.
수식 배치 등 추출 결과는 다르므로 본문이 완전히 같다는 의미로 사용하지 않는다.
원래의 `unit_id`, `parent_id`, 본문, 파일과 출처는 유지한다.
검색에서 이 ID를 이용한 중복 묶기는 별도 적용·평가가 필요하며, 현재 실행 코드는 청크를 삭제하지 않는다.
여러 페이지·표 행이 있는 다른 별표는 같은 방식으로 자동 병합하지 않는다.

## 유지해야 하는 보고서와 보완 출처

- `docs/data_reports/quality_report.json`: 원본 처리 상태와 제외 내역. 전처리할 때 갱신된다.
- `docs/data_reports/chunking_report.json`: 청크 수·길이와 무결성 검사 결과. 청킹할 때 갱신된다.
- `docs/data_reports/source_verification/consumer_dispute_standard_appendix_0002_159470209.pdf`: 환불 수식 보완에 사용하는 공식 첨부 원문. 전처리와 청킹 재실행에 필요하므로 유지한다.

이 PDF는 저장된 XML이 가리키는 공식 첨부이며 `raw/`와 별도로 보관한다. 검토한 XML·PDF 해시가 일치하는 체육시설업 표 하나에만 네 분수를 보완한다. 보완 전 본문, 수정 전후 위치, PDF 경로·해시·URL·101~102페이지 출처를 보존한다. 청크에도 `supplemental_sources`를 전달해 출처를 확인할 수 있다. 자동 환급액 계산은 활성화하지 않는다.

OCR은 수행하지 않아 이미지와 복잡한 표는 추가 검토가 필요하다. 법령은 수집 당시 버전이며 최신 법령을 재수집한 결과는 아니다.

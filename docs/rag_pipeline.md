# 데이터 전처리·청킹

3차 프로젝트처럼 자료 종류별 폴더 구성을 유지한다.
판례는 제외하고 소비자 환불·계약해지, 주택임대차, 임금체불·해고와 공통 근거 자료를 사용한다.

```text
data/
├─ raw/                           # 수집 원본
│  ├─ consumer_sources/
│  ├─ guides/
│  ├─ laws/
│  ├─ law_appendices/
│  └─ law_history/
├─ processed/                     # 원본별 전처리 JSON
│  ├─ consumer_sources/
│  ├─ guides/
│  └─ laws/
│     ├─ appendices/              # 법령 부록
│     └─ history/                 # 과거 법령: 참고용, 검색 제외
└─ chunk/                         # 종류별 검색 청크
   ├─ consumer_sources/chunks.jsonl
   ├─ guides/chunks.jsonl
   └─ laws/chunks.jsonl
```

## 실행

최초 한 번:

```powershell
uv sync
uv pip install -r requirements-ingestion.txt
```

원본 전처리만 실행 (`raw/` → `processed/`):

```powershell
uv run --no-sync python -m src.scripts.preprocess_data
```

전처리 결과로 청킹 실행 (`processed/` → `chunk/`):

```powershell
uv run --no-sync python -m src.scripts.chunk_data --size 900 --overlap 100
```

검증:

```powershell
uv run --no-sync python -m src.evaluation.retrieval_check
uv run --no-sync python -m unittest discover -s tests -v
```

### 검색 테스트 질문 추가

`tests/data/retrieval_cases.json`에는 기존 29개와 새 상담형 질문 15개, 총 44개를 저장했다.
새 질문도 소비자·주거·노동 각 5개다. 기대 근거가 확인된 43개를 점수에 반영한다.
벽지의 통상적 변색 비용 부담 질문은 구체적 근거를 확인하지 못해 검색만 실행한다.
이 질문은 evaluation_status가 needs_evidence_review이며 expected_document는 null이다.
이 상태는 정답·오답 모두 아닌 미평가로, evaluated_case_count와 unscored_case_count를 구분한다.
파일에 다음 형식의 항목을 추가하거나 별도 JSON 파일을 만든다.
expected_document는 전처리 JSON의 document_id이며, expected_locator는 해당 근거의
locator에 포함될 조문·페이지 문자열이다. 위치를 제한하지 않으면 null로 둔다.
기대 근거는 검색 실행 전에 원본을 확인해 정한다.

```json
[
  {
    "query": "주택임대차 계약 묵시적 갱신",
    "expected_document": "001248_276291_20260102",
    "expected_locator": "제6조("
  }
]
```

```powershell
uv run --no-sync python -m src.evaluation.retrieval_check --cases tests/data/retrieval_cases.json
```

옵션을 생략해도 위 파일을 사용한다. 실행하면 docs/data_reports/retrieval_report.json을 갱신한다.
보고서의 top5에는 text_preview(본문 앞 300자), text_length, preview_truncated,
parent_id가 포함된다. 미리보기만으로 조건·예외를 판단하지 말고 전체 청크와 근거도 확인한다.
expected_locator의 null은 위치 제한 없음, rank_at_5의 null은 기대 근거가 상위 5위에 없음이다.
테스트 질문이 바뀌면 점수가 달라지므로 다른 질문 집합의 점수를 단순 비교하지 않는다.

### 원본 변경 없이 검색 보완

기본 실행은 enhanced 모드이며 같은 데이터·같은 질문으로 기존 baseline 점수도 함께 기록한다.

```powershell
uv run --no-sync python -m src.evaluation.retrieval_check --mode enhanced
```

검색 코드에서만 다음을 적용한다. raw/processed/chunk 및 평가 질문·기대 근거는 바꾸지 않는다.

- 알바비·월급 → 임금, 집주인 → 임대인 등 일상 표현을 검색 어휘로 정규화.
- 원래 질문과 정규화한 질문을 각각 검색하고 순위 기반으로 결과 통합.
- 같은 전체 근거(parent)의 중복 청크 제거, 한 문서가 최대 2개 결과를 차지하도록 제한.
- 보고서에 normalized_query를 기록해 질문이 어떻게 바뀌었는지 확인 가능.

이는 어휘 검색 개선이며 임베딩·학습된 리랭커가 아니다.
정규화는 법률 판단이나 정답 문서 ID를 지정하지 않는다. 문서 다양성 제한은 여러 관련 조항을
동시에 찾아야 하는 질문에는 불리할 수도 있으므로 별도 질문으로 추가 검증해야 한다.
앞선 29개는 개선 과정에서 확인한 개발 질문이며 독립 평가셋으로 간주하지 않는다.

## 파일의 역할

다른 Python 코드에서는 아래처럼 독립적으로 호출할 수 있다.

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
print(preprocessing_result["processed_file_count"])
print(chunking_result["chunk_count"])
```

문자열 또는 Path 경로를 전달할 수 있다. 별도 raw 경로를 사용했다면 chunk_data에도
같은 raw_dir를 전달해야 원본 해시를 검증할 수 있다.
report_dir 인자로 보고서 위치를, progress 인자로 전처리 진행 메시지 함수를 지정할 수 있다.
기본적으로 Python 함수는 결과 딕셔너리를 반환하며 진행 메시지를 출력하지 않는다.
전처리 실패나 잘못된 경로·청크 설정은 예외로 호출자에게 알린다.
전처리·청킹은 각각 preprocess_data(), chunk_data()로 호출한다.
명령줄 기본 실행은 두 단계로 분리했으며 Django 연결은 추가하지 않았다.

preprocess_data.py 내부도 다음 역할로 나누었다. 외부 호출은 preprocess_data()를 사용하면 된다.

- preprocess_file(): 원본 파일 하나의 처리 결과·제외 내역 반환.
- build_pdf_entries(): PDF 페이지의 본문·표·제목 문맥과 검토 조건 구성.
- get_exclusion_reason(): 검색 제외 사유의 우선순위 판단.
- build_preprocessing_report(), save_preprocessing_report(): 보고서 구성과 저장.
- xml_extract(): XML 종류 판별. extract_law_xml()과 extract_administrative_xml()로 연결.
- extract_consumer_refund_tables(): 소비자 업종·품목 별표 선별 함수 호출.

이 분리는 전처리 기준이나 출력 데이터 구조를 바꾸지 않는다.

processed의 JSON 하나가 원본 문서 하나에 대응한다.
units에 조문·본문·표와 검토 상태를 저장하고, PDF 문서는 pages에 페이지별 상세 추출 결과도 저장한다.
검색 대상 units만 embedding_candidate가 true다. 과거 자료·서식·이미지 검토 대상 등은 false다.
청크의 parent_id는 문서의 units 안에 있는 unit_id와 연결된다.
검색 후 전체 근거를 가져와 조건·예외를 확인한다. 검색 조각만으로 수치 계산이나 법률 판단을 하지 않는다.

- `src/const/data_sources.json`: 원본별 제목·출처 URL·날짜·주제 설정. 본문은 포함하지 않는다.
- `docs/data_reports/`: 처리 제외 내역과 검색 점검 결과. 데이터 검색 경로와 분리했다.
- `src/scripts/preprocess_data.py`: 원본 XML·HTML·HWPX·PDF 추출.
- `src/scripts/data_io.py`: 자료 종류별 읽기·저장 및 원본 해시 검증.
- `src/scripts/chunk_data.py`: 문자 수·경계 기준 청킹.

저장 함수도 save_processed_data()와 save_chunks()로 분리했다.
load_processed_data()는 검색 대상으로 선별된 전처리 근거를 읽고 원본 해시를 검증한다.

## 현재 결과와 한계

원본 125개를 처리했다. 본문 추출 124개, 메타데이터만 있는 XML 1개, 처리 실패 0개다.
검색 대상 근거 3,703개와 청크 4,294개를 유지한다. 청크 본문 최대 900자, 중복 100자다.
폴더 재정리 시 근거와 청크를 ID별로 대조해 내용이 바뀌지 않았음을 확인했다.

이전 전처리 본문을 재사용하지 않고 원본을 직접 읽었다.
OCR은 수행하지 않아 이미지와 복잡한 표는 추가 검토가 필요하다.
법령은 수집 당시 버전으로, 최신 법령을 재수집한 것은 아니다.
기존 질문 14개의 hit@5는 13/14로 유지된다.
기존 어휘 검색의 전체 29개 hit@5는 20/29, MRR@5는 약 0.477이다.
같은 질문에 정규화·순위 통합·중복 정리를 적용하면 hit@5는 22/29, MRR@5는 약 0.575다.
추가 질문만 보면 소비자 4/5 유지, 주거 2/5 → 3/5, 노동 1/5 → 2/5다.
개발셋 내 개선이며 일반적인 성능 향상이나 법률 답변 정확도를 보장하지 않는다.
이후 검색 규칙을 조정하지 않고 새로 추가한 질문에서는 소비자 1/5, 주거 2/4, 노동 0/5였다.
벽지 질문 1개는 미평가다. 전체 43개 평가에서 기존 검색은 23/43(MRR 약 0.376),
개선 검색은 25/43(MRR 약 0.446)이다. 새 질문의 낮은 성적은 추가 검증이 필요함을 보여준다.
관련 안내서 등 다른 유효한 근거가 나와도 지정한 문서·조문과 다르면 실패로 잡힐 수 있으므로
보고서의 실제 본문을 함께 검토해야 한다.
질문 집합이 달라진 결과이므로 기존 14개 점수와 직접적인 성능 개선·악화로 비교하지 않는다.
작은 어휘 검색 점검이며 임베딩·답변 품질 검증을 대신하지 않는다.

재실행하면 같은 종류별 경로에 결과를 갱신한다.
원본 처리 실패가 있으면 검색용 데이터는 교체하지 않고 보고서에 남긴다.

## 코드 정리

현재 문서별 JSON과 종류별 JSONL만 지원한다. 예전 parents.jsonl·3차 전처리 형식 변환과
호환용 run(), --source 옵션은 제거했다. 저장은 save_processed_data(), save_chunks()로 분리한다.
검색 청크는 load_chunks()로 읽으며 백업 자동 탐색은 하지 않는다.

## 코드 폴더 구조

```text
src/
├─ scripts/
│  ├─ preprocess_data.py     # 원본 전처리
│  ├─ chunk_data.py          # 청킹
│  └─ data_io.py             # 읽기·저장·검증
├─ evaluation/
│  └─ retrieval_check.py     # 개발용 검색 평가
├─ rag/                     # 팀의 서비스 RAG 모듈 유지
└─ const/
   └─ data_sources.json

tests/
├─ test_preprocessing.py
├─ test_chunking.py
├─ test_retrieval.py
└─ data/retrieval_cases.json
```

기존 Django 앱과 설정, 그래프·도구 코드는 이동하지 않았다.
전처리·청킹 함수 이름과 data 폴더 내부 구조도 유지한다.

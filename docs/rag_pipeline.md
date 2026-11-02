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
uv run --no-sync python -m src.rag.raw_preprocessing
```

전처리 결과로 청킹 실행 (`processed/` → `chunk/`):

```powershell
uv run --no-sync python -m src.rag.chunking --size 900 --overlap 100
```

검증:

```powershell
uv run --no-sync python -m src.rag.retrieval_check
uv run --no-sync python -m unittest discover -s tests -v
```

## 파일의 역할

다른 Python 코드에서는 아래처럼 독립적으로 호출할 수 있다.

```python
from src.rag.raw_preprocessing import preprocess_data
from src.rag.chunking import chunk_data

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
기존 run()은 두 단계를 함께 호출하는 호환용 함수로 남겨 두었다.
명령줄 기본 실행은 두 단계로 분리했으며 Django 연결은 추가하지 않았다.

raw_preprocessing.py 내부도 다음 역할로 나누었다. 외부 호출은 preprocess_data()를 사용하면 된다.

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
- `src/rag/raw_preprocessing.py`: 원본 XML·HTML·HWPX·PDF 추출.
- `src/rag/preprocessing.py`: 전처리 결과 읽기와 원본 해시 검증.
- `src/rag/chunking.py`: 문자 수·경계 기준 청킹.
- `src/rag/data_layout.py`: 자료 종류별 저장·읽기 경로 관리.

저장 함수도 save_processed_data()와 save_chunks()로 분리했다.
load_processed_data()는 검색 대상으로 선별된 전처리 근거를 읽고 원본 해시를 검증한다.

## 현재 결과와 한계

원본 125개를 처리했다. 본문 추출 124개, 메타데이터만 있는 XML 1개, 처리 실패 0개다.
검색 대상 근거 3,703개와 청크 4,294개를 유지한다. 청크 본문 최대 900자, 중복 100자다.
폴더 재정리 시 근거와 청크를 ID별로 대조해 내용이 바뀌지 않았음을 확인했다.

이전 전처리 본문을 재사용하지 않고 원본을 직접 읽었다.
OCR은 수행하지 않아 이미지와 복잡한 표는 추가 검토가 필요하다.
법령은 수집 당시 버전으로, 최신 법령을 재수집한 것은 아니다.
개발 질문 14개의 hit@5는 13/14, MRR@5는 약 0.696이다.
작은 어휘 검색 점검이며 임베딩·답변 품질 검증을 대신하지 않는다.

재실행하면 같은 종류별 경로에 결과를 갱신한다.
원본 처리 실패가 있으면 검색용 데이터는 교체하지 않고 보고서에 남긴다.

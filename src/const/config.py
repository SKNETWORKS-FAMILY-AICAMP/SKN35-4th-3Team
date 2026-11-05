''' 경로, 변하지 않는 상수들 여기에 정의하고 다른 파일에서 import해서 사용'''

from pathlib import Path


PROJECT_PATH = Path(__file__).resolve().parent.parent.parent
# print(PROJECT_PATH) # 확인용
DEFAULT_ENV_PATH = PROJECT_PATH / ".env"

DATA_PATH = PROJECT_PATH / "data"

# Chunk된 데이터 경로
CHUNK_DATA_PATH = DATA_PATH / "chunk"
CHUNK_CONSUMER_SOURCES_DATA_PATH = CHUNK_DATA_PATH / "consumer_sources"
CHUNK_GUIDES_DATA_PATH = CHUNK_DATA_PATH / "guides"
CHUNK_LAWS_DATA_PATH = CHUNK_DATA_PATH / "laws"
CHUNK_PRECEDENTS_DATA_PATH = CHUNK_DATA_PATH / "precedents"

# 정제된 데이터 경로
PROCESSED_DATA_PATH = DATA_PATH / "processed"
PROCESSED_CONSUMER_SOURCES_DATA_PATH = PROCESSED_DATA_PATH / "consumer_sources"
PROCESSED_GUIDES_DATA_PATH = PROCESSED_DATA_PATH / "guides"
PROCESSED_LAWS_DATA_PATH = PROCESSED_DATA_PATH / "laws"
PROCESSED_PRECEDENTS_DATA_PATH = PROCESSED_DATA_PATH / "precedents"
PROCESSED_RETRIEVAL_EVALUATION_DATA_PATH = PROCESSED_DATA_PATH / "retrieval_evaluation"

# 원본 데이터 경로
RAW_DATA_PATH = DATA_PATH / "raw"
RAW_CONSUMER_SOURCES_DATA_PATH = RAW_DATA_PATH / "consumer_sources"
RAW_GUIDES_DATA_PATH = RAW_DATA_PATH / "guides"
RAW_LAW_APPENDICES_DATA_PATH = RAW_DATA_PATH / "law_appendices"
RAW_LAW_HISTORY_DATA_PATH = RAW_DATA_PATH / "law_history"
RAW_LAWS_DATA_PATH = RAW_DATA_PATH / "laws"
RAW_PRECEDENTS_DATA_PATH = RAW_DATA_PATH / "precedents"

# 테스트 결과 경로
TEST_PATH = PROJECT_PATH / "tests"
TEST_REPORT_PATH = TEST_PATH / "report"

# 환경 파일 경로
ENV_PATH = PROJECT_PATH / ".env"
ENV_EXAMPLE_PATH = PROJECT_PATH / ".env.example"

# 공통 평가 입력과 리랭크 Markdown 보고서·모델 캐시 경로
RAG_EVALUATION_CASES_PATH = PROJECT_PATH / "rag_evaluation_cases.review.json"
RAG_RERANK_REPORT_PATH = PROJECT_PATH / "docs" / "rag_rerank_evaluation" / "rag_rerank_report.md"
RERANK_CACHE_PATH = PROJECT_PATH / ".cache" / "rerank"


# ==========================================================================================

EMBED_DIMENSION = 1536
EMBED_NORMALIZED = True

# 빈 검색 결과에 대해 허용할 추가 재검색 횟수입니다.
RAG_MAX_RETRIES = 2

# 실제 Qdrant·Generator·Citation 통합 테스트 결과를 저장합니다.
RAG_CITATION_REPORT_PATH = TEST_REPORT_PATH / "rag_citation_report.md"
RAG_GRAPH_REPORT_PATH = TEST_REPORT_PATH / "rag_graph_integration_report.md"
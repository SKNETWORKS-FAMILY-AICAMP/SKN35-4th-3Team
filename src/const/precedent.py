"""판례 검증 MCP에서 공통으로 사용하는 고정값."""

from pathlib import Path


PROJECT_PATH = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_PATH = PROJECT_PATH / ".env"

LAW_API_OC_ENV = "LAW_API_OC"
LAW_API_BASE_URL = "https://www.law.go.kr/DRF"
LAW_API_SEARCH_URL = f"{LAW_API_BASE_URL}/lawSearch.do"
LAW_API_DETAIL_URL = f"{LAW_API_BASE_URL}/lawService.do"
LAW_PRECEDENT_SOURCE_URL = (
    "https://www.law.go.kr/LSW/precInfoP.do?precSeq={precedent_id}"
)

DEFAULT_TOP_N = 5
DEFAULT_CANDIDATE_LIMIT = 20
MAX_TOP_N = 20
MAX_CANDIDATES = 100

MCP_RESPONSE_STATUSES = frozenset(
    {
        "success",
        "empty_result",
        "connection_error",
        "invalid_request",
        "internal_error",
    }
)

PRECEDENT_VERIFICATION_STATUSES = frozenset(
    {
        "verified",
        "partial_match",
        "not_found",
        "mismatch",
        "ambiguous",
        "unavailable",
    }
)

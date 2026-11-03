"""판례 MCP Tool의 입력 검증과 문자열 JSON 응답 계약.

서비스 업무 로직과 SDK 등록부가 같은 입력 범위, 상태값, 직렬화 규칙을
사용하도록 MCP 경계의 형식만 이 모듈에서 관리합니다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from src.const.precedent import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_TOP_N,
    MAX_CANDIDATES,
    MAX_TOP_N,
    MCP_RESPONSE_STATUSES,
)


def _validated_result_limits(
    top_n: object,
    candidate_limit: object,
) -> tuple[int, int]:
    """최종 결과 수와 검색 후보 수의 공통 범위를 검증합니다."""

    if (
        isinstance(top_n, bool)
        or not isinstance(top_n, int)
        or not 1 <= top_n <= MAX_TOP_N
    ):
        raise ValueError(f"top_n은 1 이상 {MAX_TOP_N} 이하의 정수여야 합니다.")
    if (
        isinstance(candidate_limit, bool)
        or not isinstance(candidate_limit, int)
        or not top_n <= candidate_limit <= MAX_CANDIDATES
    ):
        raise ValueError(
            f"candidate_limit은 top_n 이상 {MAX_CANDIDATES} 이하의 정수여야 합니다."
        )
    return top_n, candidate_limit


@dataclass(frozen=True)
class FindSimilarPrecedentsRequest:
    """`find_similar_precedents` Tool의 정규화된 입력."""

    query: str
    top_n: int
    candidate_limit: int

    @classmethod
    def create(
        cls,
        query: object,
        *,
        top_n: object = DEFAULT_TOP_N,
        candidate_limit: object = DEFAULT_CANDIDATE_LIMIT,
    ) -> FindSimilarPrecedentsRequest:
        top_n, candidate_limit = _validated_result_limits(
            top_n,
            candidate_limit,
        )
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query는 비어 있지 않은 문자열이어야 합니다.")
        return cls(
            query=query.strip(),
            top_n=top_n,
            candidate_limit=candidate_limit,
        )


@dataclass(frozen=True)
class ComparePrecedentSimilarityRequest:
    """`compare_precedent_similarity` Tool의 정규화된 입력."""

    candidate: Mapping[str, object]
    top_n: int
    candidate_limit: int

    @classmethod
    def create(
        cls,
        candidate: object,
        *,
        top_n: object = DEFAULT_TOP_N,
        candidate_limit: object = DEFAULT_CANDIDATE_LIMIT,
    ) -> ComparePrecedentSimilarityRequest:
        top_n, candidate_limit = _validated_result_limits(
            top_n,
            candidate_limit,
        )
        if not isinstance(candidate, Mapping):
            raise TypeError("candidate는 객체여야 합니다.")
        return cls(
            candidate=dict(candidate),
            top_n=top_n,
            candidate_limit=candidate_limit,
        )


def serialize_tool_response(status: str, **fields: object) -> str:
    """MCP 응답을 한글이 보존되는 JSON 문자열로 만듭니다."""

    if status not in MCP_RESPONSE_STATUSES:
        raise ValueError(f"지원하지 않는 MCP status입니다: {status}")
    return json.dumps(
        {"status": status, **fields},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_tool_response(response: str) -> dict[str, object]:
    """MCP JSON 문자열과 외부 status를 검증합니다."""

    if not isinstance(response, str) or not response.strip():
        raise ValueError("MCP 응답은 비어 있지 않은 문자열이어야 합니다.")
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError as error:
        raise ValueError("MCP 응답이 올바른 JSON이 아닙니다.") from error
    if (
        not isinstance(parsed, dict)
        or parsed.get("status") not in MCP_RESPONSE_STATUSES
    ):
        raise ValueError("MCP 응답의 status가 없거나 지원되지 않습니다.")
    return parsed

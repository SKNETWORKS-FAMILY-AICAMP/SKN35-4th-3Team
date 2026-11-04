"""SDK 등록부와 분리된 Qdrant 우선 판례 검색 업무 서비스.

일반 검색은 ``질문 → Qdrant 검색 → 정렬·반환`` 순서로 처리합니다. 기준 판례
비교는 Qdrant에서 기준 판례를 먼저 찾고, 없을 때만 공식 API로 보완합니다.
MCP SDK Tool 등록은 ``src.tools.mcp_runtime``이 담당합니다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.const.precedent import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_TOP_N,
)
from src.tools.precedent_contracts import (
    ComparePrecedentSimilarityRequest,
    FindSimilarPrecedentsRequest,
    parse_tool_response,
    serialize_tool_response,
)
from src.tools.precedent_api import (
    PrecedentApiError,
    PrecedentApiLike,
)
from src.tools.precedent_verification import (
    PrecedentCandidate,
    find_similar_precedents,
    format_similarity_percent,
    verify_precedent,
    verify_precedent_candidates,
)
from src.tools.qdrant_precedent import (
    PrecedentRetrieverLike,
    PrecedentSearchError,
)


logger = logging.getLogger(__name__)

_QDRANT_RESULT_FIELDS = {
    "ranking_source": "qdrant",
    "score_type": "qdrant_cosine_similarity",
    "score_description": (
        "4차 컬렉션에 복사된 판례 OpenAI 벡터의 Cosine 유사도이며 정답 확률이 아닙니다."
    ),
}


def _reference_search_text(official: Mapping[str, object]) -> str:
    """기준 판례에서 의미 검색에 필요한 핵심 내용만 조합합니다."""

    fields = (
        official.get("case_name"),
        official.get("holding"),
        official.get("summary"),
        official.get("reference_articles"),
        official.get("comparison_text"),
        official.get("text"),
        str(official.get("body") or "")[:4000],
    )
    search_text = " ".join(
        str(value).strip()
        for value in fields
        if value is not None and str(value).strip()
    )
    if not search_text:
        raise ValueError("기준 판례에 유사도 검색용 내용이 없습니다.")
    return search_text


def _error_status(error: PrecedentApiError) -> str:
    """공식 API 오류 코드를 MCP 외부 상태값으로 변환합니다."""

    if error.code == "connection_error":
        return "connection_error"
    if error.code == "not_found":
        return "empty_result"
    return "internal_error"


def _invalid_request_response(error: Exception) -> str:
    """잘못된 Tool 입력을 일관된 MCP 응답으로 변환합니다."""

    return serialize_tool_response(
        "invalid_request",
        error={"message": str(error)},
    )


def _api_error_response(error: PrecedentApiError) -> str:
    """공식 판례 API 오류를 안전한 MCP 응답으로 변환합니다."""

    return serialize_tool_response(
        _error_status(error),
        error={"code": error.code, "message": error.safe_message},
    )


def _internal_error_response(message: str) -> str:
    """예상하지 못한 내부 오류를 기록하고 안전한 응답을 만듭니다."""

    logger.exception(message)
    return serialize_tool_response(
        "internal_error",
        error={"message": message},
    )


def _search_error_response(error: PrecedentSearchError) -> str:
    """Qdrant·임베딩 검색 오류를 안전한 MCP 응답으로 변환합니다."""

    status = (
        "connection_error"
        if error.code == "connection_error"
        else "internal_error"
    )
    return serialize_tool_response(
        status,
        error={"code": error.code, "message": error.safe_message},
    )


def _result_status(results: object) -> str:
    """결과 존재 여부를 MCP 성공 상태로 변환합니다."""

    return "success" if results else "empty_result"


def _rank_qdrant_candidates(
    candidates: Sequence[Mapping[str, object]],
    top_n: int,
) -> list[dict[str, object]]:
    """Qdrant 순서를 유지하며 화면 표시용 순위와 유사도를 추가합니다."""

    results = [dict(candidate) for candidate in candidates[:top_n]]
    for rank, result in enumerate(results, start=1):
        score = result.get("similarity_score")
        result["similarity_rank"] = rank
        result["similarity_percent"] = format_similarity_percent(
            float(score) if isinstance(score, (int, float)) else None
        )
    return results


@dataclass
class MCPToolService:
    """MCP SDK와 분리해 Django·테스트에서도 재사용하는 판례 검색 서비스."""

    api: PrecedentApiLike
    retriever: PrecedentRetrieverLike | None = None

    def health_check(self) -> str:
        """공식 판례 API 연결 상태를 MCP 응답으로 반환합니다."""

        try:
            healthy = self.api.health_check()
        except (TypeError, ValueError) as error:
            return _invalid_request_response(error)
        except PrecedentApiError as error:
            return _api_error_response(error)
        except Exception:
            return _internal_error_response(
                "공식 판례 API 상태 확인에 실패했습니다."
            )
        return serialize_tool_response("success", healthy=bool(healthy))

    def get_precedent_detail(self, precedent_id: str) -> str:
        """판례정보일련번호로 공식 판례 상세를 조회합니다."""

        try:
            precedent = self.api.get_detail(precedent_id)
        except (TypeError, ValueError) as error:
            return _invalid_request_response(error)
        except PrecedentApiError as error:
            return _api_error_response(error)
        except Exception:
            return _internal_error_response("공식 판례 상세 조회에 실패했습니다.")
        return serialize_tool_response(
            "success",
            precedent=precedent.to_dict(),
        )

    def verify_precedent(
        self,
        candidate: Mapping[str, object],
        *,
        query: str | None = None,
    ) -> str:
        """판례 후보 한 건의 식별정보를 공식 판례와 대조합니다."""

        try:
            normalized = PrecedentCandidate.from_mapping(
                candidate,
                default_rank=1,
            )
            result = verify_precedent(self.api, normalized, query=query)
        except (TypeError, ValueError) as error:
            return _invalid_request_response(error)
        except Exception:
            return _internal_error_response("판례 검증 처리에 실패했습니다.")
        return serialize_tool_response("success", result=result)

    def verify_precedent_candidates(
        self,
        candidates: Sequence[Mapping[str, object]],
        *,
        top_n: int = DEFAULT_TOP_N,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        query: str | None = None,
    ) -> str:
        """여러 판례 후보를 공식 검증하고 Top-N으로 반환합니다."""

        try:
            result = verify_precedent_candidates(
                self.api,
                candidates,
                top_n=top_n,
                candidate_limit=candidate_limit,
                query=query,
            )
        except (TypeError, ValueError) as error:
            return _invalid_request_response(error)
        except Exception:
            return _internal_error_response("판례 후보 일괄 검증에 실패했습니다.")

        return serialize_tool_response(
            _result_status(result["results"]),
            **result,
        )

    def find_similar_precedents(
        self,
        query: str,
        *,
        top_n: int = DEFAULT_TOP_N,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> str:
        """Qdrant에서 질문과 유사한 판례 Top-N을 바로 반환합니다."""

        try:
            request = FindSimilarPrecedentsRequest.create(
                query,
                top_n=top_n,
                candidate_limit=candidate_limit,
            )
            if self.retriever is None:
                # 1. 공식 API 검색 → 2. 질문 유사도 계산 → 3. 정렬
                result = find_similar_precedents(
                    self.api,
                    request.query,
                    top_n=request.top_n,
                    candidate_limit=request.candidate_limit,
                )
            else:
                # 1. 4차 Qdrant에서 판례 필터로 유사한 후보를 검색합니다.
                candidates = self.retriever.search_candidates(
                    request.query,
                    limit=request.candidate_limit,
                )
                # 2. Qdrant 순서를 유지하며 최종 Top-N을 만듭니다.
                results = _rank_qdrant_candidates(candidates, request.top_n)
                result = {
                    "query": request.query,
                    "requested_top_n": request.top_n,
                    "searched_count": len(candidates),
                    "results": results,
                    "excluded": [],
                    **_QDRANT_RESULT_FIELDS,
                }
        except (TypeError, ValueError) as error:
            return _invalid_request_response(error)
        except PrecedentSearchError as error:
            return _search_error_response(error)
        except PrecedentApiError as error:
            return _api_error_response(error)
        except Exception:
            return _internal_error_response(
                "Qdrant 판례 유사도 검색에 실패했습니다."
            )

        return serialize_tool_response(_result_status(result["results"]), **result)

    def compare_precedent_similarity(
        self,
        candidate: Mapping[str, object],
        *,
        top_n: int = DEFAULT_TOP_N,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> str:
        """기준 판례를 Qdrant 우선으로 찾고 유사 판례를 반환합니다."""

        if self.retriever is None:
            return serialize_tool_response(
                "internal_error",
                error={"message": "Qdrant 판례 검색기가 설정되지 않았습니다."},
            )

        try:
            request = ComparePrecedentSimilarityRequest.create(
                candidate,
                top_n=top_n,
                candidate_limit=candidate_limit,
            )
            # 1. 입력받은 기준 판례를 정규화합니다.
            normalized = PrecedentCandidate.from_mapping(
                request.candidate,
                default_rank=1,
            )
            # 2. Qdrant에서 기준 판례를 먼저 정확 조회합니다.
            reference = self.retriever.lookup_candidate(request.candidate)
            if reference is None:
                # 3. Qdrant에 없을 때만 공식 API로 기준 판례를 보완합니다.
                verified_reference = verify_precedent(self.api, normalized)
                official = verified_reference.get("official")
                if not isinstance(official, Mapping):
                    return serialize_tool_response(
                        "empty_result",
                        reference_precedent=verified_reference,
                        similar_precedents=[],
                        excluded=[],
                    )
                reference = {**official, "lookup_source": "official_api"}

            # 4. 기준 판례 내용으로 유사 판례를 Qdrant에서 검색합니다.
            precedent_id = reference.get("precedent_id")
            excluded_id = (
                precedent_id.strip()
                if isinstance(precedent_id, str) and precedent_id.strip()
                else None
            )
            search_text = _reference_search_text(reference)
            candidates = self.retriever.search_candidates(
                search_text,
                limit=request.candidate_limit,
                exclude_precedent_id=excluded_id,
            )
            results = _rank_qdrant_candidates(candidates, request.top_n)
        except (TypeError, ValueError) as error:
            return _invalid_request_response(error)
        except PrecedentSearchError as error:
            return _search_error_response(error)
        except PrecedentApiError as error:
            return _api_error_response(error)
        except Exception:
            return _internal_error_response(
                "기준 판례 유사도 비교에 실패했습니다."
            )

        return serialize_tool_response(
            _result_status(results),
            reference_precedent=reference,
            similar_precedents=results,
            excluded=[],
            requested_top_n=request.top_n,
            searched_count=len(candidates),
            **_QDRANT_RESULT_FIELDS,
        )


def create_tool_service(
    api: PrecedentApiLike,
    retriever: PrecedentRetrieverLike | None = None,
) -> MCPToolService:
    """MCP Tool 등록과 테스트에서 공통으로 사용할 서비스를 만듭니다."""

    return MCPToolService(api=api, retriever=retriever)

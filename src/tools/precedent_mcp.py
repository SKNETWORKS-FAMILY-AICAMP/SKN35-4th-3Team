"""SDK 등록부와 분리된 Qdrant 우선 판례 검색 업무 서비스.

일반 검색과 기준 판례 비교 모두 Qdrant를 우선 사용하고, 최종 결과가 부족할
때만 공식 API 후보로 보완합니다. 기준 판례는 후보 검색이 끝난 뒤 제외합니다.
MCP SDK Tool 등록은 ``src.tools.mcp_runtime``이 담당합니다.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.const.precedent import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_TOP_N,
    MAX_CANDIDATES,
    MIN_QDRANT_PRECEDENT_SIMILARITY,
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
        result.setdefault("ranking_source", "qdrant")
        result.setdefault("score_type", "qdrant_cosine_similarity")
        score = result.get("similarity_score")
        result["similarity_rank"] = rank
        result["similarity_percent"] = format_similarity_percent(
            float(score) if isinstance(score, (int, float)) else None
        )
    return results


def _identity_keys(candidate: Mapping[str, object]) -> set[str]:
    """판례 ID·사건번호·공식 링크를 중복 제거용 키로 정규화합니다."""

    keys: set[str] = set()
    for field in ("precedent_id", "case_number", "source_url"):
        value = candidate.get(field)
        if isinstance(value, str) and value.strip():
            keys.add(f"{field}:{value.strip().lower()}")
    return keys


def _normalize_official_candidates(
    result: Mapping[str, object],
) -> list[dict[str, object]]:
    """공식 API 후보를 기존 MCP 문서 형식과 호환되게 정규화합니다."""

    raw_candidates = result.get("results", [])
    if not isinstance(raw_candidates, list):
        raise ValueError("공식 API 판례 검색 응답 형식이 잘못됐습니다.")

    normalized: list[dict[str, object]] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, Mapping):
            continue
        candidate = dict(raw_candidate)
        precedent_id = candidate.get("precedent_id")
        if isinstance(precedent_id, str) and precedent_id.strip():
            candidate.setdefault(
                "document_id",
                f"precedent:{precedent_id.strip()}",
            )
        candidate["ranking_source"] = "official_api"
        candidate["score_type"] = (
            candidate.get("mcp_score_type")
            or result.get("score_type")
            or "official_text_similarity"
        )
        score = candidate.get("mcp_similarity_score")
        if isinstance(score, (int, float)):
            candidate["similarity_score"] = float(score)
            candidate["similarity_percent"] = candidate.get(
                "mcp_similarity_percent"
            ) or format_similarity_percent(float(score))
        normalized.append(candidate)
    return normalized


def _merge_candidates(
    *candidate_groups: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """앞선 검색 출처의 순서를 유지하며 동일 판례를 한 번만 남깁니다."""

    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for group in candidate_groups:
        for raw_candidate in group:
            candidate = dict(raw_candidate)
            keys = _identity_keys(candidate)
            if keys and seen.intersection(keys):
                continue
            seen.update(keys)
            merged.append(candidate)
    return merged


def _exclude_reference(
    candidates: Sequence[Mapping[str, object]],
    reference: Mapping[str, object] | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """후보 통합 후 기준 판례와 식별자가 같은 결과를 제외합니다."""

    reference_keys = _identity_keys(reference) if reference else set()
    if not reference_keys:
        return [dict(candidate) for candidate in candidates], []

    kept: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        if reference_keys.intersection(_identity_keys(candidate)):
            excluded.append({
                **candidate,
                "exclusion_reason": "reference_precedent",
            })
        else:
            kept.append(candidate)
    return kept, excluded


def _filter_qdrant_similarity(
    candidates: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """최소 유사도 이상인 Qdrant 판례만 유효 후보로 분류합니다."""

    kept: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        score = candidate.get("similarity_score")
        is_valid_score = (
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(float(score))
        )
        if is_valid_score and float(score) >= MIN_QDRANT_PRECEDENT_SIMILARITY:
            kept.append(candidate)
            continue
        excluded.append({
            **candidate,
            "exclusion_reason": "low_similarity",
            "minimum_similarity_score": MIN_QDRANT_PRECEDENT_SIMILARITY,
        })
    return kept, excluded


def _finalize_candidates(
    candidates: Sequence[Mapping[str, object]],
    top_n: int,
) -> list[dict[str, object]]:
    """출처별 순서를 유지하고 최종 표시 순위만 다시 부여합니다."""

    results = [dict(candidate) for candidate in candidates[:top_n]]
    for rank, result in enumerate(results, start=1):
        result["similarity_rank"] = rank
        score = result.get("similarity_score")
        if "similarity_percent" not in result:
            result["similarity_percent"] = format_similarity_percent(
                float(score) if isinstance(score, (int, float)) else None
            )
    return results


@dataclass
class MCPToolService:
    """MCP SDK와 분리해 Django·테스트에서도 재사용하는 판례 검색 서비스."""

    api: PrecedentApiLike
    retriever: PrecedentRetrieverLike | None = None

    def _search_with_fallback(
        self,
        query: str,
        *,
        top_n: int,
        candidate_limit: int,
        reference: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Qdrant 결과를 우선하고 부족한 수량만 공식 API로 보완합니다."""

        if self.retriever is None:
            return find_similar_precedents(
                self.api,
                query,
                top_n=top_n,
                candidate_limit=candidate_limit,
            )

        search_limit = min(
            MAX_CANDIDATES,
            max(candidate_limit, top_n + (1 if reference else 0)),
        )
        qdrant_candidates = self.retriever.search_candidates(
            query,
            limit=search_limit,
        )
        qdrant_ranked = _rank_qdrant_candidates(
            qdrant_candidates,
            search_limit,
        )
        qdrant_kept, qdrant_reference_excluded = _exclude_reference(
            qdrant_ranked,
            reference,
        )
        qdrant_qualified, qdrant_similarity_excluded = (
            _filter_qdrant_similarity(qdrant_kept)
        )
        qdrant_excluded = [
            *qdrant_reference_excluded,
            *qdrant_similarity_excluded,
        ]

        official_result: dict[str, object] = {
            "results": [],
            "excluded": [],
            "searched_count": 0,
        }
        fallback_error: dict[str, str] | None = None
        fallback_used = len(qdrant_qualified) < top_n
        if fallback_used:
            official_top_n = min(
                search_limit,
                top_n + (1 if reference else 0),
            )
            try:
                official_result = find_similar_precedents(
                    self.api,
                    query,
                    top_n=official_top_n,
                    candidate_limit=search_limit,
                )
            except PrecedentApiError as error:
                fallback_error = {
                    "code": error.code,
                    "message": error.safe_message,
                }

        official_candidates = _normalize_official_candidates(official_result)
        official_kept, official_reference_excluded = _exclude_reference(
            official_candidates,
            reference,
        )
        merged = _merge_candidates(qdrant_qualified, official_kept)
        results = _finalize_candidates(merged, top_n)
        official_added_count = max(0, len(merged) - len(qdrant_qualified))

        official_excluded = official_result.get("excluded", [])
        excluded = [*qdrant_excluded, *official_reference_excluded]
        if isinstance(official_excluded, list):
            excluded.extend(
                dict(item)
                for item in official_excluded
                if isinstance(item, Mapping)
            )

        if official_added_count and qdrant_qualified:
            ranking_fields = {
                "ranking_source": "qdrant_then_official_api",
                "score_type": "source_specific",
                "score_description": (
                    "Qdrant Cosine 순위를 우선하고 부족한 결과를 공식 판례 "
                    "텍스트 유사도순으로 보완했습니다. 서로 다른 점수를 직접 "
                    "비교하지 않습니다."
                ),
            }
        elif official_added_count:
            ranking_fields = {
                "ranking_source": "official_api",
                "score_type": "official_text_similarity",
                "score_description": official_result.get("score_description", ""),
            }
        else:
            ranking_fields = dict(_QDRANT_RESULT_FIELDS)

        return {
            "query": query,
            "requested_top_n": top_n,
            "searched_count": len(qdrant_candidates) + int(
                official_result.get("searched_count", 0) or 0
            ),
            "qdrant_searched_count": len(qdrant_candidates),
            "qdrant_qualified_count": len(qdrant_qualified),
            "qdrant_low_similarity_count": len(qdrant_similarity_excluded),
            "minimum_qdrant_similarity": MIN_QDRANT_PRECEDENT_SIMILARITY,
            "official_searched_count": int(
                official_result.get("searched_count", 0) or 0
            ),
            "fallback_used": fallback_used,
            "fallback_error": fallback_error,
            "results": results,
            "excluded": excluded,
            **ranking_fields,
        }

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
        """Qdrant 우선으로 질문과 유사한 판례 Top-N을 반환합니다."""

        try:
            request = FindSimilarPrecedentsRequest.create(
                query,
                top_n=top_n,
                candidate_limit=candidate_limit,
            )
            result = self._search_with_fallback(
                request.query,
                top_n=request.top_n,
                candidate_limit=request.candidate_limit,
            )
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

            # 4. 기준 판례 내용으로 Qdrant 우선 유사 검색을 실행합니다.
            search_text = _reference_search_text(reference)
            search_result = self._search_with_fallback(
                search_text,
                top_n=request.top_n,
                candidate_limit=request.candidate_limit,
                reference=reference,
            )
            results = search_result["results"]
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
            excluded=search_result["excluded"],
            **{
                key: value
                for key, value in search_result.items()
                if key not in {"query", "results", "excluded"}
            },
        )


def create_tool_service(
    api: PrecedentApiLike,
    retriever: PrecedentRetrieverLike | None = None,
) -> MCPToolService:
    """MCP Tool 등록과 테스트에서 공통으로 사용할 서비스를 만듭니다."""

    return MCPToolService(api=api, retriever=retriever)

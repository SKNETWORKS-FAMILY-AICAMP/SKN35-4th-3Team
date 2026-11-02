"""판례 검증 업무 로직과 MCP 외부 응답 계약."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.const.precedent import MCP_RESPONSE_STATUSES
from src.tools.precedent_api import (
    PrecedentApiError,
    PrecedentApiLike,
)
from src.tools.precedent_verification import (
    PrecedentCandidate,
    find_similar_precedents,
    verify_precedent,
    verify_precedent_candidates,
)
from src.tools.qdrant_precedent import (
    PrecedentRetrieverLike,
    PrecedentSearchError,
)


logger = logging.getLogger(__name__)


def _reference_search_text(official: Mapping[str, object]) -> str:
    """기준 판례에서 의미 검색에 필요한 핵심 내용만 조합합니다."""

    fields = (
        official.get("case_name"),
        official.get("holding"),
        official.get("summary"),
        official.get("reference_articles"),
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
    if not isinstance(parsed, dict) or parsed.get("status") not in MCP_RESPONSE_STATUSES:
        raise ValueError("MCP 응답의 status가 없거나 지원되지 않습니다.")
    return parsed


def _error_status(error: PrecedentApiError) -> str:
    if error.code == "connection_error":
        return "connection_error"
    if error.code == "not_found":
        return "empty_result"
    return "internal_error"


def _invalid_request(error: Exception) -> str:
    return serialize_tool_response(
        "invalid_request",
        error={"message": str(error)},
    )


def _api_error(error: PrecedentApiError) -> str:
    return serialize_tool_response(
        _error_status(error),
        error={"code": error.code, "message": error.safe_message},
    )


def _internal_error(message: str) -> str:
    logger.exception(message)
    return serialize_tool_response(
        "internal_error",
        error={"message": message},
    )


@dataclass
class MCPToolService:
    """MCP SDK와 분리해 Django·테스트에서도 재사용하는 판례 검증 서비스."""

    api: PrecedentApiLike
    retriever: PrecedentRetrieverLike | None = None

    def health_check(self) -> str:
        try:
            healthy = self.api.health_check()
        except (TypeError, ValueError) as error:
            return _invalid_request(error)
        except PrecedentApiError as error:
            return _api_error(error)
        except Exception:
            return _internal_error("공식 판례 API 상태 확인에 실패했습니다.")
        return serialize_tool_response("success", healthy=bool(healthy))

    def get_precedent_detail(self, precedent_id: str) -> str:
        try:
            precedent = self.api.get_detail(precedent_id)
        except (TypeError, ValueError) as error:
            return _invalid_request(error)
        except PrecedentApiError as error:
            return _api_error(error)
        except Exception:
            return _internal_error("공식 판례 상세 조회에 실패했습니다.")
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
        try:
            normalized = PrecedentCandidate.from_mapping(
                candidate,
                default_rank=1,
            )
            result = verify_precedent(self.api, normalized, query=query)
        except (TypeError, ValueError) as error:
            return _invalid_request(error)
        except Exception:
            return _internal_error("판례 검증 처리에 실패했습니다.")
        return serialize_tool_response("success", result=result)

    def verify_precedent_candidates(
        self,
        candidates: Sequence[Mapping[str, object]],
        *,
        top_n: int = 5,
        candidate_limit: int = 20,
        query: str | None = None,
    ) -> str:
        try:
            result = verify_precedent_candidates(
                self.api,
                candidates,
                top_n=top_n,
                candidate_limit=candidate_limit,
                query=query,
            )
        except (TypeError, ValueError) as error:
            return _invalid_request(error)
        except Exception:
            return _internal_error("판례 후보 일괄 검증에 실패했습니다.")

        status = "success" if result["results"] else "empty_result"
        return serialize_tool_response(status, **result)

    def find_similar_precedents(
        self,
        query: str,
        *,
        top_n: int = 5,
        candidate_limit: int = 20,
    ) -> str:
        """3차 Qdrant 후보를 공식 API로 검증해 유사한 Top-N을 반환합니다."""

        try:
            if self.retriever is None:
                result = find_similar_precedents(
                    self.api,
                    query,
                    top_n=top_n,
                    candidate_limit=candidate_limit,
                )
            else:
                candidates = self.retriever.search_candidates(
                    query,
                    limit=candidate_limit,
                )
                result = verify_precedent_candidates(
                    self.api,
                    candidates,
                    top_n=top_n,
                    candidate_limit=candidate_limit,
                    # Qdrant 의미 유사도 순위를 유지하고 공식 API는 존재 여부와
                    # 식별정보만 검증합니다.
                    query=None,
                )
                result.update(
                    {
                        "query": query.strip(),
                        "searched_count": len(candidates),
                        "ranking_source": "qdrant",
                        "score_type": "qdrant_cosine_similarity",
                        "score_description": (
                            "3차 Qdrant OpenAI 벡터의 Cosine 유사도이며 "
                            "정답 확률이 아닙니다."
                        ),
                    }
                )
        except (TypeError, ValueError) as error:
            return _invalid_request(error)
        except PrecedentSearchError as error:
            status = (
                "connection_error"
                if error.code == "connection_error"
                else "internal_error"
            )
            return serialize_tool_response(
                status,
                error={"code": error.code, "message": error.safe_message},
            )
        except PrecedentApiError as error:
            return _api_error(error)
        except Exception:
            return _internal_error("공식 판례 유사도 검색에 실패했습니다.")

        status = "success" if result["results"] else "empty_result"
        return serialize_tool_response(status, **result)

    def compare_precedent_similarity(
        self,
        candidate: Mapping[str, object],
        *,
        top_n: int = 5,
        candidate_limit: int = 20,
    ) -> str:
        """기준 판례와 유사한 Qdrant 판례를 공식 검증 후 반환합니다."""

        if self.retriever is None:
            return serialize_tool_response(
                "internal_error",
                error={"message": "Qdrant 판례 검색기가 설정되지 않았습니다."},
            )

        try:
            normalized = PrecedentCandidate.from_mapping(
                candidate,
                default_rank=1,
            )
            reference = verify_precedent(self.api, normalized)
            official = reference.get("official")
            if not isinstance(official, Mapping):
                return serialize_tool_response(
                    "empty_result",
                    reference_precedent=reference,
                    similar_precedents=[],
                    excluded=[],
                )

            precedent_id = official.get("precedent_id")
            if not isinstance(precedent_id, str) or not precedent_id.strip():
                raise ValueError("공식 기준 판례의 precedent_id가 없습니다.")
            search_text = _reference_search_text(official)
            candidates = self.retriever.search_candidates(
                search_text,
                limit=candidate_limit,
                exclude_precedent_id=precedent_id.strip(),
            )
            comparison = verify_precedent_candidates(
                self.api,
                candidates,
                top_n=top_n,
                candidate_limit=candidate_limit,
                query=None,
            )
        except (TypeError, ValueError) as error:
            return _invalid_request(error)
        except PrecedentSearchError as error:
            status = (
                "connection_error"
                if error.code == "connection_error"
                else "internal_error"
            )
            return serialize_tool_response(
                status,
                error={"code": error.code, "message": error.safe_message},
            )
        except Exception:
            return _internal_error("기준 판례 유사도 비교에 실패했습니다.")

        status = "success" if comparison["results"] else "empty_result"
        return serialize_tool_response(
            status,
            reference_precedent=reference,
            similar_precedents=comparison["results"],
            excluded=comparison["excluded"],
            requested_top_n=top_n,
            checked_count=comparison["checked_count"],
            verified_count=comparison["verified_count"],
            ranking_source="qdrant",
            score_type="qdrant_cosine_similarity",
            score_description=(
                "3차 Qdrant OpenAI 벡터의 Cosine 유사도이며 "
                "정답 확률이 아닙니다."
            ),
        )


def create_tool_service(
    api: PrecedentApiLike,
    retriever: PrecedentRetrieverLike | None = None,
) -> MCPToolService:
    """MCP Tool 등록과 테스트에서 공통으로 사용할 서비스를 만듭니다."""

    return MCPToolService(api=api, retriever=retriever)

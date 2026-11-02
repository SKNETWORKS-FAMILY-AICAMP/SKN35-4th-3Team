"""공식 판례를 검증하고 사용자 질문과의 유사도를 계산합니다."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from src.const.precedent import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_TOP_N,
    MAX_CANDIDATES,
    MAX_TOP_N,
)
from src.tools.precedent_api import (
    OfficialPrecedent,
    PrecedentApiError,
    PrecedentApiLike,
)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("판례 식별 메타데이터는 문자열이어야 합니다.")
    normalized = value.strip()
    return normalized or None


def _metadata(candidate: Mapping[str, object]) -> Mapping[str, object]:
    value = candidate.get("metadata")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("candidate.metadata는 객체여야 합니다.")
    return value


def _first(
    candidate: Mapping[str, object],
    metadata: Mapping[str, object],
    *names: str,
) -> object:
    for name in names:
        if name in candidate and candidate[name] is not None:
            return candidate[name]
        if name in metadata and metadata[name] is not None:
            return metadata[name]
    return None


def _positive_int(value: object, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name}는 양의 정수여야 합니다.")
    return value


def format_similarity_percent(
    score: float | None,
    *,
    digits: int = 1,
) -> str | None:
    """0~1 유사도를 화면에 표시할 퍼센트 문자열로 변환합니다."""

    if score is None:
        return None
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("유사도 점수는 숫자 또는 None이어야 합니다.")
    if not math.isfinite(float(score)):
        raise ValueError("유사도 점수는 유한한 숫자여야 합니다.")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 4:
        raise ValueError("digits는 0 이상 4 이하의 정수여야 합니다.")

    # UI 표시용 값일 뿐 정답 확률이나 법률적 신뢰 확률을 의미하지 않습니다.
    bounded_score = min(max(float(score), 0.0), 1.0)
    return f"{bounded_score * 100:.{digits}f}%"


@dataclass(frozen=True)
class PrecedentCandidate:
    """사용자 또는 외부 검색 단계가 MCP에 전달하는 판례 후보."""

    similarity_score: float | None
    retrieval_rank: int
    score_type: str | None
    precedent_id: str | None = None
    case_number: str | None = None
    case_name: str | None = None
    court: str | None = None
    decision_date: str | None = None
    source_url: str | None = None
    comparison_text: str | None = None
    extra: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        candidate: Mapping[str, object],
        *,
        default_rank: int,
    ) -> PrecedentCandidate:
        if not isinstance(candidate, Mapping):
            raise TypeError("각 candidate는 객체여야 합니다.")
        metadata = _metadata(candidate)

        raw_score = _first(
            candidate,
            metadata,
            "similarity_score",
            "rerank_score",
            "score",
        )
        similarity_score: float | None = None
        if raw_score is not None:
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise TypeError("candidate의 유사도 점수는 숫자여야 합니다.")
            similarity_score = float(raw_score)
            if not math.isfinite(similarity_score):
                raise ValueError("candidate의 유사도 점수는 유한한 숫자여야 합니다.")

        rank = _positive_int(
            _first(candidate, metadata, "retrieval_rank", "rank"),
            "retrieval_rank",
            default_rank,
        )
        score_type = _optional_text(_first(candidate, metadata, "score_type"))
        if similarity_score is not None and score_type is None:
            score_type = "external_similarity"
        precedent_id = _optional_text(
            _first(candidate, metadata, "precedent_id")
        )
        case_number = _optional_text(
            _first(candidate, metadata, "case_number")
        )
        case_name = _optional_text(_first(candidate, metadata, "case_name"))
        if not any((precedent_id, case_number, case_name)):
            raise ValueError(
                "candidate에는 precedent_id, case_number, case_name 중 하나가 필요합니다."
            )

        known = {
            "similarity_score",
            "rerank_score",
            "score",
            "retrieval_rank",
            "rank",
            "score_type",
            "precedent_id",
            "case_number",
            "case_name",
            "court",
            "decision_date",
            "source_url",
            "comparison_text",
            "user_query",
            "query",
            "description",
            "text",
            "metadata",
        }
        return cls(
            similarity_score=similarity_score,
            retrieval_rank=rank,
            score_type=score_type,
            precedent_id=precedent_id,
            case_number=case_number,
            case_name=case_name,
            court=_optional_text(_first(candidate, metadata, "court")),
            decision_date=_optional_text(
                _first(candidate, metadata, "decision_date")
            ),
            source_url=_optional_text(_first(candidate, metadata, "source_url")),
            comparison_text=_optional_text(
                _first(
                    candidate,
                    metadata,
                    "comparison_text",
                    "user_query",
                    "query",
                    "description",
                    "text",
                )
            ),
            extra={
                str(key): value
                for key, value in candidate.items()
                if key not in known
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "precedent_id": self.precedent_id,
            "case_number": self.case_number,
            "case_name": self.case_name,
            "court": self.court,
            "decision_date": self.decision_date,
            "source_url": self.source_url,
            "similarity_score": self.similarity_score,
            "similarity_percent": format_similarity_percent(
                self.similarity_score
            ),
            "external_similarity_score": self.similarity_score,
            "retrieval_rank": self.retrieval_rank,
            "score_type": self.score_type,
            "external_score_type": self.score_type,
            "comparison_text": self.comparison_text,
            **dict(self.extra),
        }


def _identity_text(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).casefold()


def _date_text(value: object) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _tokens(value: object) -> list[str]:
    return re.findall(r"[0-9A-Za-z가-힣]{2,}", str(value or "").casefold())


def _character_ngrams(value: object, size: int = 3) -> list[str]:
    normalized = _identity_text(value)
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]
    return [
        normalized[index : index + size]
        for index in range(len(normalized) - size + 1)
    ]


def _cosine(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    left_counts = Counter(left)
    right_counts = Counter(right)
    numerator = sum(
        count * right_counts.get(token, 0)
        for token, count in left_counts.items()
    )
    left_norm = math.sqrt(sum(count * count for count in left_counts.values()))
    right_norm = math.sqrt(sum(count * count for count in right_counts.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def text_similarity(left: object, right: object) -> float:
    """한국어 단어와 문자 3-gram 코사인을 결합한 설명 가능한 유사도."""

    token_score = _cosine(_tokens(left), _tokens(right))
    character_score = _cosine(
        _character_ngrams(left),
        _character_ngrams(right),
    )
    return round(0.65 * token_score + 0.35 * character_score, 6)


def official_similarity_breakdown(
    query: str,
    official: OfficialPrecedent,
) -> dict[str, object]:
    """사용자 질문과 공식 판례 필드별 유사도를 설명 가능한 형태로 반환합니다."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query는 비어 있지 않은 문자열이어야 합니다.")
    field_scores = {
        "case_name": text_similarity(query, official.case_name),
        "holding": text_similarity(query, official.holding),
        "summary": text_similarity(query, official.summary),
        "reference_articles": text_similarity(
            query,
            official.reference_articles,
        ),
        "body": text_similarity(query, official.body[:4000]),
    }
    best_field, best_score = max(
        field_scores.items(),
        key=lambda item: (item[1], item[0]),
    )
    normalized_score = round(best_score, 6)
    return {
        "mcp_similarity_score": normalized_score,
        "mcp_similarity_percent": format_similarity_percent(normalized_score),
        "mcp_score_type": "official_text_similarity",
        "best_matching_field": best_field,
        "field_scores": field_scores,
    }


def official_query_similarity(
    query: str,
    official: OfficialPrecedent,
) -> float:
    """사용자 질문과 공식 판례 사이의 0~1 범위 유사도를 반환합니다."""

    return float(official_similarity_breakdown(query, official)["mcp_similarity_score"])


_QUERY_STOPWORDS = frozenset(
    {
        "관련",
        "판례",
        "사건",
        "경우",
        "대한",
        "대해",
        "어떤",
        "알려줘",
        "찾아줘",
        "있는지",
        "무엇인지",
    }
)


def _official_search_queries(query: str) -> tuple[str, ...]:
    """자연어 질문을 공식 API 원문·핵심어 검색어로 제한적으로 확장합니다."""

    normalized = " ".join(query.split())
    queries = [normalized]
    tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", normalized)
    for token in tokens:
        if token in _QUERY_STOPWORDS or token in queries:
            continue
        queries.append(token)
        if len(queries) == 5:
            break
    return tuple(queries)


def _case_number_matches(candidate: str, official: str) -> bool:
    expected = _identity_text(candidate)
    if not expected:
        return False
    official_parts = {
        _identity_text(part)
        for part in re.split(r"[,/\n]", official)
        if _identity_text(part)
    }
    return expected in official_parts or expected == _identity_text(official)


def _search_match_score(
    candidate: PrecedentCandidate,
    official: OfficialPrecedent,
) -> int:
    score = 0
    if candidate.case_number and _case_number_matches(
        candidate.case_number,
        official.case_number,
    ):
        score += 8
    if candidate.case_name and _identity_text(candidate.case_name) == _identity_text(
        official.case_name
    ):
        score += 4
    if candidate.court and _identity_text(candidate.court) == _identity_text(
        official.court
    ):
        score += 2
    if candidate.decision_date and _date_text(candidate.decision_date) == _date_text(
        official.decision_date
    ):
        score += 2
    return score


def _resolve_official(
    api: PrecedentApiLike,
    candidate: PrecedentCandidate,
) -> OfficialPrecedent:
    if candidate.precedent_id:
        return api.get_detail(candidate.precedent_id)

    query = candidate.case_number or candidate.case_name
    assert query is not None
    search_results = api.search(query, display=20)
    if not search_results:
        raise PrecedentApiError(
            "not_found",
            "공식 API에서 일치하는 판례를 찾지 못했습니다.",
        )

    scored = [
        (_search_match_score(candidate, official), official)
        for official in search_results
    ]
    best_score = max(score for score, _official in scored)
    best = [official for score, official in scored if score == best_score]
    minimum_identity_score = 8 if candidate.case_number else 4
    if best_score < minimum_identity_score:
        raise PrecedentApiError(
            "not_found",
            "공식 API 검색 결과에서 판례 식별값이 일치하지 않았습니다.",
        )
    if len(best) != 1:
        raise PrecedentApiError(
            "ambiguous",
            "동일한 판례 후보가 여러 건이어서 하나로 확정할 수 없습니다.",
        )
    return api.get_detail(best[0].precedent_id)


def _comparison(
    candidate: PrecedentCandidate,
    official: OfficialPrecedent,
) -> dict[str, bool]:
    comparisons: dict[str, bool] = {}
    if candidate.precedent_id:
        comparisons["precedent_id"] = candidate.precedent_id == official.precedent_id
    if candidate.case_number:
        comparisons["case_number"] = _case_number_matches(
            candidate.case_number,
            official.case_number,
        )
    if candidate.case_name:
        comparisons["case_name"] = _identity_text(
            candidate.case_name
        ) == _identity_text(official.case_name)
    if candidate.court:
        comparisons["court"] = _identity_text(candidate.court) == _identity_text(
            official.court
        )
    if candidate.decision_date:
        comparisons["decision_date"] = _date_text(
            candidate.decision_date
        ) == _date_text(official.decision_date)
    return comparisons


_IDENTITY_WEIGHTS = {
    "precedent_id": 0.45,
    "case_number": 0.30,
    "case_name": 0.15,
    "court": 0.05,
    "decision_date": 0.05,
}


def _identity_similarity(comparisons: Mapping[str, bool]) -> float | None:
    """후보가 제공한 식별 필드만 기준으로 가중 일치율을 계산합니다."""

    available_weight = sum(_IDENTITY_WEIGHTS[name] for name in comparisons)
    if not available_weight:
        return None
    matched_weight = sum(
        _IDENTITY_WEIGHTS[name]
        for name, matched in comparisons.items()
        if matched
    )
    return round(matched_weight / available_weight, 6)


def _combined_similarity(
    identity_score: float | None,
    content_score: float | None,
) -> float | None:
    if identity_score is None:
        return content_score
    if content_score is None:
        return identity_score
    return round(0.7 * identity_score + 0.3 * content_score, 6)


def verify_precedent(
    api: PrecedentApiLike,
    candidate: PrecedentCandidate,
    *,
    query: str | None = None,
) -> dict[str, object]:
    """판례 한 건을 공식 API와 대조합니다."""

    try:
        official = _resolve_official(api, candidate)
    except PrecedentApiError as error:
        status = (
            error.code
            if error.code in {"not_found", "mismatch", "ambiguous"}
            else "unavailable"
        )
        return {
            **candidate.to_dict(),
            "verified": False,
            "verification_status": status,
            "verification_message": error.safe_message,
            "matched_fields": [],
            "mismatched_fields": [],
        }

    comparisons = _comparison(candidate, official)
    comparison_text = query or candidate.comparison_text
    content_breakdown = (
        official_similarity_breakdown(comparison_text, official)
        if comparison_text
        else None
    )
    identity_similarity = _identity_similarity(comparisons)
    content_similarity = (
        float(content_breakdown["mcp_similarity_score"])
        if content_breakdown is not None
        else None
    )
    mcp_similarity = _combined_similarity(
        identity_similarity,
        content_similarity,
    )
    identity_field = (
        "precedent_id"
        if candidate.precedent_id
        else "case_number"
        if candidate.case_number
        else "case_name"
    )
    matched_fields = [name for name, matched in comparisons.items() if matched]
    mismatched_fields = [
        name for name, matched in comparisons.items() if not matched
    ]
    if not comparisons.get(identity_field, False):
        status = "mismatch"
    elif mismatched_fields:
        status = "partial_match"
    else:
        status = "verified"

    return {
        **candidate.to_dict(),
        "candidate": candidate.to_dict(),
        "precedent_id": official.precedent_id,
        "case_number": official.case_number,
        "case_name": official.case_name,
        "court": official.court,
        "decision_date": official.decision_date,
        "verified": status == "verified",
        "verification_status": status,
        "verification_message": (
            "공식 판례의 식별 정보가 모두 일치합니다."
            if status == "verified"
            else "공식 판례는 확인했지만 일부 메타데이터가 일치하지 않습니다."
            if status == "partial_match"
            else "공식 판례와 핵심 식별 정보가 일치하지 않습니다."
        ),
        "matched_fields": matched_fields,
        "mismatched_fields": mismatched_fields,
        "identity_match_score": identity_similarity,
        "content_similarity_score": content_similarity,
        "mcp_similarity_score": mcp_similarity,
        "mcp_similarity_percent": format_similarity_percent(mcp_similarity),
        "mcp_score_type": "official_identity_and_text_similarity",
        "similarity_breakdown": content_breakdown,
        "official": official.to_dict(),
        "source_url": official.source_url,
    }


def verify_precedent_candidates(
    api: PrecedentApiLike,
    candidates: Sequence[Mapping[str, object]],
    *,
    top_n: int = DEFAULT_TOP_N,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    query: str | None = None,
) -> dict[str, object]:
    """외부 판례 후보를 공식 검증하고 MCP 유사도순 Top-N으로 반환합니다."""

    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise TypeError("candidates는 판례 후보 배열이어야 합니다.")
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"candidates는 최대 {MAX_CANDIDATES}건까지 지원합니다.")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= MAX_TOP_N:
        raise ValueError(f"top_n은 1 이상 {MAX_TOP_N} 이하의 정수여야 합니다.")
    if (
        isinstance(candidate_limit, bool)
        or not isinstance(candidate_limit, int)
        or not top_n <= candidate_limit <= MAX_CANDIDATES
    ):
        raise ValueError(
            f"candidate_limit은 top_n 이상 {MAX_CANDIDATES} 이하의 정수여야 합니다."
        )
    if not candidates:
        return {
            "score_type": None,
            "requested_top_n": top_n,
            "checked_count": 0,
            "verified_count": 0,
            "results": [],
            "excluded": [],
        }

    normalized = [
        PrecedentCandidate.from_mapping(candidate, default_rank=index)
        for index, candidate in enumerate(candidates, start=1)
    ]
    score_types = {
        candidate.score_type
        for candidate in normalized
        if candidate.score_type is not None
    }
    if len(score_types) != 1:
        raise ValueError(
            "Top-N 비교에는 모든 후보가 동일한 score_type을 사용해야 합니다."
        )

    ordered = sorted(
        normalized,
        key=lambda candidate: (
            -(candidate.similarity_score or 0.0),
            candidate.retrieval_rank,
            candidate.precedent_id or candidate.case_number or candidate.case_name or "",
        ),
    )[:candidate_limit]

    verified: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    checked_count = 0
    for candidate in ordered:
        checked_count += 1
        result = verify_precedent(api, candidate, query=query)
        if result["verification_status"] == "verified":
            verified.append(result)
        else:
            excluded.append(result)

    ranking_field = "mcp_similarity_score" if query else "similarity_score"
    verified.sort(
        key=lambda result: (
            -float(result.get(ranking_field) or 0.0),
            int(result.get("retrieval_rank") or 0),
            str(result.get("precedent_id") or ""),
        )
    )
    verified = verified[:top_n]

    for rank, result in enumerate(verified, start=1):
        result["verified_rank"] = rank

    return {
        "score_type": (
            "official_identity_and_text_similarity"
            if query
            else next(iter(score_types), None)
        ),
        "ranking_source": "mcp" if query else "external",
        "requested_top_n": top_n,
        "checked_count": checked_count,
        "verified_count": len(verified),
        "results": verified,
        "excluded": excluded,
    }


def find_similar_precedents(
    api: PrecedentApiLike,
    query: str,
    *,
    top_n: int = DEFAULT_TOP_N,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> dict[str, object]:
    """공식 API만 이용해 질문과 유사한 판례 Top-N을 계산합니다."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query는 비어 있지 않은 문자열이어야 합니다.")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= MAX_TOP_N:
        raise ValueError(f"top_n은 1 이상 {MAX_TOP_N} 이하의 정수여야 합니다.")
    if (
        isinstance(candidate_limit, bool)
        or not isinstance(candidate_limit, int)
        or not top_n <= candidate_limit <= MAX_CANDIDATES
    ):
        raise ValueError(
            f"candidate_limit은 top_n 이상 {MAX_CANDIDATES} 이하의 정수여야 합니다."
        )

    search_queries = _official_search_queries(query)
    search_results: list[OfficialPrecedent] = []
    search_result_ids: set[str] = set()
    for search_query in search_queries:
        for result in api.search(search_query, display=candidate_limit):
            if result.precedent_id in search_result_ids:
                continue
            search_result_ids.add(result.precedent_id)
            search_results.append(result)
            if len(search_results) == candidate_limit:
                break
        if len(search_results) == candidate_limit:
            break
    seen: set[str] = set()
    scored: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    for summary in search_results:
        if summary.precedent_id in seen:
            continue
        seen.add(summary.precedent_id)
        try:
            official = api.get_detail(summary.precedent_id)
        except PrecedentApiError as error:
            excluded.append(
                {
                    "precedent_id": summary.precedent_id,
                    "verification_status": "unavailable",
                    "error": {
                        "code": error.code,
                        "message": error.safe_message,
                    },
                }
            )
            continue

        similarity = official_similarity_breakdown(query, official)
        scored.append(
            {
                **official.to_dict(),
                **similarity,
                "verified": True,
                "verification_status": "verified",
            }
        )

    scored.sort(
        key=lambda result: (
            -float(result["mcp_similarity_score"]),
            str(result["precedent_id"]),
        )
    )
    results = scored[:top_n]
    for rank, result in enumerate(results, start=1):
        result["similarity_rank"] = rank

    return {
        "query": query.strip(),
        "search_queries": list(search_queries),
        "score_type": "official_text_similarity",
        "score_description": (
            "공식 판례 필드와 질문의 단어·문자 3-gram 코사인 유사도이며 "
            "정답 확률이 아닙니다."
        ),
        "requested_top_n": top_n,
        "searched_count": len(search_results),
        "checked_count": len(scored) + len(excluded),
        "results": results,
        "excluded": excluded,
    }

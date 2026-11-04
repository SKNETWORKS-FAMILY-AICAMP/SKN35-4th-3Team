"""4차 Qdrant 컬렉션에서 판례 후보만 읽기 전용으로 검색합니다.

질문 임베딩 → ``document_type=precedent`` 필터 검색 → 비판례 재검증 →
판례별 최고 점수 청크 병합 → 순위 부여 순서로 후보를 만듭니다.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from src.const.precedent import DEFAULT_ENV_PATH, LAW_PRECEDENT_SOURCE_URL


DEFAULT_COLLECTION = "SKN35-4th-3Team"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSION = 1536
_PRECEDENT_DOCUMENT_TYPE = "precedent"


class PrecedentSearchError(RuntimeError):
    """외부 검색 오류를 비밀값이 없는 코드와 메시지로 전달합니다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class QdrantClientLike(Protocol):
    def query_points(self, **kwargs: object) -> object: ...

    def scroll(self, **kwargs: object) -> object: ...


class PrecedentRetrieverLike(Protocol):
    def lookup_candidate(
        self,
        candidate: Mapping[str, object],
    ) -> Mapping[str, object] | None: ...

    def search_candidates(
        self,
        query: str,
        *,
        limit: int,
        exclude_precedent_id: str | None = None,
    ) -> Sequence[Mapping[str, object]]: ...


def _text(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} 환경변수가 필요합니다.")
    return value


def _response_points(response: object) -> Sequence[object]:
    points = getattr(response, "points", response)
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        raise PrecedentSearchError(
            "invalid_response",
            "Qdrant 판례 검색 결과 형식이 올바르지 않습니다.",
        )
    return points


def _scroll_points(response: object) -> Sequence[object]:
    """Qdrant scroll 응답에서 Point 배열만 검증해 꺼냅니다."""

    points = response[0] if isinstance(response, tuple) else response
    points = getattr(points, "points", points)
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        raise PrecedentSearchError(
            "invalid_response",
            "Qdrant 기준 판례 조회 결과 형식이 올바르지 않습니다.",
        )
    return points


def _precedent_filter(**matches: str) -> object:
    """판례 자료형과 추가 식별 조건을 묶은 Qdrant 필터를 만듭니다."""

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    conditions = {
        "document_type": _PRECEDENT_DOCUMENT_TYPE,
        **matches,
    }
    return Filter(
        must=[
            FieldCondition(key=field, match=MatchValue(value=value))
            for field, value in conditions.items()
        ]
    )


def _candidate_identity(
    candidate: Mapping[str, object],
) -> tuple[str, str]:
    """기준 판례 정확 조회에 사용할 첫 번째 식별정보를 선택합니다."""

    if not isinstance(candidate, Mapping):
        raise TypeError("candidate는 객체여야 합니다.")
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    for field in ("precedent_id", "case_number", "case_name"):
        value = _text(candidate.get(field) or metadata.get(field))
        if value:
            return field, value
    raise ValueError(
        "candidate에는 precedent_id, case_number, case_name 중 하나가 필요합니다."
    )


@dataclass
class QdrantPrecedentRetriever:
    """질문 임베딩과 4차 Qdrant 컬렉션을 연결하는 판례 검색기."""

    client: QdrantClientLike
    embed_query: Callable[[str], Sequence[float]]
    collection_name: str = DEFAULT_COLLECTION
    vector_name: str | None = None

    def _create_query_vector(self, query: str) -> tuple[float, ...]:
        """검색 질문을 4차 컬렉션 규격의 임베딩으로 변환합니다."""

        try:
            vector = tuple(float(value) for value in self.embed_query(query))
        except Exception:
            raise PrecedentSearchError(
                "connection_error",
                "OpenAI 질문 임베딩 생성에 실패했습니다.",
            ) from None

        if len(vector) != DEFAULT_EMBEDDING_DIMENSION or not all(
            math.isfinite(value) for value in vector
        ):
            raise PrecedentSearchError(
                "invalid_response",
                "질문 임베딩이 4차 Qdrant 벡터 규격과 일치하지 않습니다.",
            )
        return vector

    def _query_options(
        self,
        vector: Sequence[float],
        limit: int,
    ) -> dict[str, object]:
        """판례만 반환하도록 Qdrant 검색 옵션을 만듭니다."""

        options: dict[str, object] = {
            "collection_name": self.collection_name,
            "query": list(vector),
            "query_filter": _precedent_filter(),
            # 한 판례가 여러 청크일 수 있어 넉넉히 받은 뒤 판례 단위로 합칩니다.
            "limit": min(max(limit * 4, 20), 100),
            "with_payload": True,
            "with_vectors": False,
        }
        if self.vector_name:
            options["using"] = self.vector_name
        return options

    @staticmethod
    def _candidate_from_point(
        point: object,
        excluded_id: str | None,
    ) -> tuple[str, dict[str, object]] | None:
        """Qdrant Point를 판례 후보로 변환하고 비판례는 제외합니다."""

        payload = dict(getattr(point, "payload", None) or {})
        if _text(payload.get("document_type")) != _PRECEDENT_DOCUMENT_TYPE:
            return None

        precedent_id = _text(payload.get("precedent_id"))
        document_id = _text(payload.get("document_id"))
        case_number = _text(payload.get("case_number"))
        case_name = _text(payload.get("case_name") or payload.get("title"))
        if not any((precedent_id, case_number, case_name)):
            return None
        if excluded_id is not None and precedent_id == excluded_id:
            return None

        score = float(getattr(point, "score", 0.0))
        if not math.isfinite(score):
            return None

        point_id = str(point.id)
        group_key = precedent_id or document_id or case_number or point_id
        source_url = _text(payload.get("source_url"))
        if source_url is None and precedent_id is not None:
            source_url = LAW_PRECEDENT_SOURCE_URL.format(
                precedent_id=precedent_id
            )
        candidate = {
            "precedent_id": precedent_id,
            "case_number": case_number,
            "case_name": case_name,
            "court": _text(payload.get("court")),
            "decision_date": _text(
                payload.get("decision_date") or payload.get("judgment_date")
            ),
            "source_url": source_url,
            "holding": _text(payload.get("holding")),
            "summary": _text(payload.get("summary")),
            "reference_articles": _text(payload.get("reference_articles")),
            "body": _text(
                payload.get("body")
                or payload.get("text")
                or payload.get("content")
            ),
            "similarity_score": score,
            "qdrant_similarity_score": score,
            "score_type": "qdrant_cosine_similarity",
            "point_id": point_id,
            "document_id": document_id,
            "chunk_id": _text(payload.get("chunk_id")),
        }
        return group_key, candidate

    def lookup_candidate(
        self,
        candidate: Mapping[str, object],
    ) -> dict[str, object] | None:
        """판례 식별정보로 Qdrant Payload를 먼저 정확 조회합니다."""

        field, value = _candidate_identity(candidate)
        try:
            response = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=_precedent_filter(**{field: value}),
                limit=20,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            raise PrecedentSearchError(
                "connection_error",
                "Qdrant 기준 판례 조회에 실패했습니다.",
            ) from None

        reference: dict[str, object] | None = None
        bodies: list[str] = []
        for point in _scroll_points(response):
            parsed = self._candidate_from_point(point, None)
            if parsed is None:
                continue
            _, current = parsed
            if reference is None:
                reference = current
            body = current.get("body")
            if isinstance(body, str) and body not in bodies:
                bodies.append(body)

        if reference is None:
            return None
        if bodies:
            reference["body"] = "\n".join(bodies)
        reference["lookup_source"] = "qdrant"
        return reference

    def search_candidates(
        self,
        query: str,
        *,
        limit: int,
        exclude_precedent_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """질문과 유사한 판례 후보를 점수순으로 반환합니다."""

        # 1. 입력을 검증하고 질문 임베딩을 생성합니다.
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query는 비어 있지 않은 문자열이어야 합니다.")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit은 1 이상 100 이하의 정수여야 합니다.")
        normalized_query = query.strip()
        excluded_id = _text(exclude_precedent_id)
        vector = self._create_query_vector(normalized_query)
        options = self._query_options(vector, limit)

        # 2. Qdrant에서 document_type=precedent인 Point만 조회합니다.
        try:
            response = self.client.query_points(**options)
        except Exception:
            raise PrecedentSearchError(
                "connection_error",
                "Qdrant 판례 검색에 실패했습니다.",
            ) from None

        # 3. 비판례를 다시 제외하고 판례별 최고 점수 청크만 남깁니다.
        grouped: dict[str, dict[str, object]] = {}
        for point in _response_points(response):
            parsed = self._candidate_from_point(point, excluded_id)
            if parsed is None:
                continue
            group_key, candidate = parsed
            score = float(candidate["similarity_score"])
            previous = grouped.get(group_key)
            if previous is None or score > float(previous["similarity_score"]):
                grouped[group_key] = candidate

        # 4. 유사도순으로 정렬하고 최종 검색 순위를 부여합니다.
        ordered = sorted(
            grouped.values(),
            key=lambda item: (
                -float(item["similarity_score"]),
                str(item.get("precedent_id") or item.get("document_id") or ""),
            ),
        )[:limit]
        for rank, candidate in enumerate(ordered, start=1):
            candidate["retrieval_rank"] = rank
        return tuple(ordered)


def create_precedent_retriever(
    env_path: str | Path | None = None,
) -> QdrantPrecedentRetriever:
    """4차 OpenAI 컬렉션 규격으로 읽기 전용 판례 검색기를 만듭니다."""

    load_dotenv(
        Path(env_path) if env_path is not None else DEFAULT_ENV_PATH,
        override=False,
    )
    model = os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
    dimension_text = os.getenv(
        "EMBEDDING_DIMENSION",
        str(DEFAULT_EMBEDDING_DIMENSION),
    ).strip()
    try:
        dimension = int(dimension_text)
    except ValueError:
        raise ValueError("EMBEDDING_DIMENSION은 정수여야 합니다.") from None
    if model != DEFAULT_EMBEDDING_MODEL or dimension != DEFAULT_EMBEDDING_DIMENSION:
        raise ValueError(
            "4차 Qdrant 검색은 text-embedding-3-small, 1536차원만 지원합니다."
        )

    from openai import OpenAI
    from qdrant_client import QdrantClient

    openai_client = OpenAI(api_key=_required_env("OPENAI_API_KEY"))
    qdrant_client = QdrantClient(
        url=_required_env("QDRANT_URL"),
        api_key=_required_env("QDRANT_API_KEY"),
        timeout=30,
        prefer_grpc=False,
    )

    def embed_query(text: str) -> Sequence[float]:
        response = openai_client.embeddings.create(
            model=model,
            input=text,
            dimensions=dimension,
        )
        return response.data[0].embedding

    return QdrantPrecedentRetriever(
        client=qdrant_client,
        embed_query=embed_query,
        collection_name=(
            os.getenv("QDRANT_COLLECTION", DEFAULT_COLLECTION).strip()
            or DEFAULT_COLLECTION
        ),
        vector_name=os.getenv("QDRANT_VECTOR_NAME", "").strip() or None,
    )

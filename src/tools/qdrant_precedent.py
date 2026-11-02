"""3차 프로젝트 Qdrant에서 판례 후보를 읽기 전용으로 검색합니다."""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from src.const.precedent import DEFAULT_ENV_PATH


DEFAULT_COLLECTION = "team_documents_openai_dev"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSION = 1536


class PrecedentSearchError(RuntimeError):
    """외부 검색 오류를 비밀값이 없는 코드와 메시지로 전달합니다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class QdrantClientLike(Protocol):
    def query_points(self, **kwargs: object) -> object: ...


class PrecedentRetrieverLike(Protocol):
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


@dataclass
class QdrantPrecedentRetriever:
    """질문 임베딩과 기존 Qdrant 컬렉션을 연결하는 판례 검색기."""

    client: QdrantClientLike
    embed_query: Callable[[str], Sequence[float]]
    collection_name: str = DEFAULT_COLLECTION
    vector_name: str | None = None

    def search_candidates(
        self,
        query: str,
        *,
        limit: int,
        exclude_precedent_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query는 비어 있지 않은 문자열이어야 합니다.")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit은 1 이상 100 이하의 정수여야 합니다.")
        excluded_id = _text(exclude_precedent_id)

        try:
            vector = tuple(float(value) for value in self.embed_query(query.strip()))
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
                "질문 임베딩이 3차 Qdrant 벡터 규격과 일치하지 않습니다.",
            )

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        options: dict[str, object] = {
            "collection_name": self.collection_name,
            "query": list(vector),
            "query_filter": Filter(
                must=[
                    FieldCondition(
                        key="document_type",
                        match=MatchValue(value="precedent"),
                    )
                ]
            ),
            # 한 판례가 여러 청크일 수 있어 넉넉히 받은 뒤 판례 단위로 합칩니다.
            "limit": min(max(limit * 4, 20), 100),
            "with_payload": True,
            "with_vectors": False,
        }
        if self.vector_name:
            options["using"] = self.vector_name

        try:
            response = self.client.query_points(**options)
        except Exception:
            raise PrecedentSearchError(
                "connection_error",
                "Qdrant 판례 검색에 실패했습니다.",
            ) from None

        grouped: dict[str, dict[str, object]] = {}
        for point in _response_points(response):
            payload = dict(getattr(point, "payload", None) or {})
            precedent_id = _text(payload.get("precedent_id"))
            document_id = _text(payload.get("document_id"))
            case_number = _text(payload.get("case_number"))
            case_name = _text(payload.get("case_name") or payload.get("title"))
            if not any((precedent_id, case_number, case_name)):
                continue
            if excluded_id is not None and precedent_id == excluded_id:
                continue

            score = float(getattr(point, "score", 0.0))
            if not math.isfinite(score):
                continue
            group_key = precedent_id or document_id or case_number or str(point.id)
            candidate = {
                "precedent_id": precedent_id,
                "case_number": case_number,
                "case_name": case_name,
                "court": _text(payload.get("court")),
                "decision_date": _text(
                    payload.get("decision_date") or payload.get("judgment_date")
                ),
                "source_url": _text(payload.get("source_url")),
                "similarity_score": score,
                "qdrant_similarity_score": score,
                "score_type": "qdrant_cosine_similarity",
                "point_id": str(point.id),
                "document_id": document_id,
                "chunk_id": _text(payload.get("chunk_id")),
            }
            previous = grouped.get(group_key)
            if previous is None or score > float(previous["similarity_score"]):
                grouped[group_key] = candidate

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
    """3차 OpenAI 컬렉션 규격으로 읽기 전용 판례 검색기를 만듭니다."""

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
            "3차 Qdrant 검색은 text-embedding-3-small, 1536차원만 지원합니다."
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

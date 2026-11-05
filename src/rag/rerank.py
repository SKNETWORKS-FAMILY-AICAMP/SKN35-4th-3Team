"""RRF 검색 결과를 Cross-Encoder로 리랭킹합니다."""

import math
import time
from functools import lru_cache
from typing import Any

from sentence_transformers import CrossEncoder


MODEL_NAME = "BAAI/bge-reranker-v2-m3"


# ==================================================
# 1. Cross-Encoder 모델 로딩 (최초 1회)
# ==================================================

@lru_cache(maxsize=1)
def get_rerank() -> CrossEncoder:
    return CrossEncoder(
        MODEL_NAME,
        device="cpu",
        max_length=1024,
    )


# ==================================================
# 2. 리랭킹 입력 텍스트 구성
# ==================================================

def build_rerank_text(doc: dict[str, Any]) -> str:
    """법률명 + 조문 위치 + 본문을 결합합니다."""

    metadata = doc.get("metadata") or doc.get("payload") or {}

    if not isinstance(metadata, dict):
        metadata = {}

    text = (
        doc.get("text")
        or metadata.get("text")
        or metadata.get("embedding_text")
        or ""
    )

    if not isinstance(text, str) or not text.strip():
        return ""

    source_name = metadata.get("source_name")
    locator = metadata.get("locator")

    parts = []

    if isinstance(source_name, str) and source_name.strip():
        parts.append(f"법률명/출처: {source_name.strip()}")

    if isinstance(locator, str) and locator.strip():
        parts.append(f"조문/위치: {locator.strip()}")

    parts.append(f"본문: {text.strip()}")

    return "\n".join(parts)


# ==================================================
# 3. Cross-Encoder 리랭킹
# ==================================================

def rerank(
    query: str,
    documents: list[dict[str, Any]],
    *,
    top_k: int = 10,
    batch_size: int = 8,
) -> list[dict[str, Any]]:
    """
    RRF 순위 50% + Cross-Encoder 순위 50%를 반영합니다.

    Args:
        query: 원본 사용자 질문
        documents: RRF 점수 내림차순 문서 목록
        top_k: 최종 반환 문서 수
        batch_size: 모델 추론 배치 크기

    Returns:
        결합 점수 내림차순 상위 K개 문서
    """

    # 입력 검증
    if not isinstance(query, str) or not query.strip():
        raise ValueError("질문은 비어 있지 않은 문자열이어야 합니다.")

    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k는 1 이상의 정수여야 합니다.")

    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size는 1 이상의 정수여야 합니다.")

    if not isinstance(documents, list):
        raise TypeError("documents는 리스트여야 합니다.")

    if not documents:
        return []

    query = query.strip()

    valid_documents = []
    pairs = []

    # 1. 문서 텍스트 구성 및 RRF 순위 기록
    for rrf_rank, doc in enumerate(documents, start=1):

        if not isinstance(doc, dict):
            raise TypeError("각 문서는 딕셔너리여야 합니다.")

        rerank_text = build_rerank_text(doc)

        if not rerank_text:
            continue

        valid_doc = {
            **doc,
            "rrf_rank": rrf_rank,
            "rrf_score": doc.get(
                "rrf_score",
                doc.get("score"),
            ),
        }

        valid_documents.append(valid_doc)
        pairs.append((query, rerank_text))

    if not pairs:
        return []

    # 2. Cross-Encoder 점수 계산
    model = get_rerank()

    scores = model.predict(
        pairs,
        batch_size=batch_size,
        show_progress_bar=False,
    )

    # 3. CE 점수 저장
    reranked = []

    for doc, score in zip(valid_documents, scores):

        score = float(score)

        if not math.isfinite(score):
            raise ValueError(
                "유효하지 않은 리랭킹 점수가 발생했습니다."
            )

        reranked.append({
            **doc,
            "rerank_score": score,
        })

    # 4. CE 점수 기준 순위 계산
    reranked.sort(
        key=lambda doc: doc["rerank_score"],
        reverse=True,
    )

    for rank, doc in enumerate(reranked, start=1):
        doc["ce_rank"] = rank

    # 5. RRF 50% + CE 50% 순위 결합
    alpha = 0.5

    for doc in reranked:
        doc["final_score"] = (
            alpha * (1 / doc["ce_rank"])
            + (1 - alpha) * (1 / doc["rrf_rank"])
        )

    # 6. 결합 점수 내림차순 정렬
    reranked.sort(
        key=lambda doc: doc["final_score"],
        reverse=True,
    )

    # 7. 최종 순위 기록
    for rank, doc in enumerate(reranked, start=1):
        doc["rerank_rank"] = rank

    return reranked[:top_k]


# ==================================================
# 4. 직접 실행 시 테스트
# ==================================================

if __name__ == "__main__":

    from src.rag.hybrid_search import hybrid_search
    from src.rag.rrf import rrf

    query = "온라인 쇼핑몰에서 구매한 상품을 환불받을 수 있나요?"

    search_top_k = 40
    rerank_top_k = 10
    generator_top_k = 5

    print("=" * 70)
    print("Cross-Encoder + RRF 결합 리랭킹 테스트")
    print(f"모델: {MODEL_NAME}")
    print(f"질문: {query}")
    print("=" * 70)

    total_start = time.perf_counter()

    # 1. Qdrant Hybrid Search
    print("\n[1] Qdrant Hybrid Search")

    search_results = hybrid_search(
        query=query,
        top_k=search_top_k,
    )

    rankings = {
        "q1_dense": search_results["dense_results"],
        "q1_bm25": search_results["bm25_results"],
    }

    print(f"Dense: {len(rankings['q1_dense'])}개")
    print(f"BM25: {len(rankings['q1_bm25'])}개")

    # 2. RRF 통합
    print("\n[2] RRF 통합")

    rrf_results = rrf({"rankings": rankings})["documents"]

    print(f"RRF 후보: {len(rrf_results)}개")

    # 3. Cross-Encoder + RRF 리랭킹
    print("\n[3] Cross-Encoder + RRF 리랭킹")

    rerank_start = time.perf_counter()

    reranked_results = rerank(
        query=query,
        documents=rrf_results,
        top_k=rerank_top_k,
        batch_size=8,
    )

    rerank_elapsed = time.perf_counter() - rerank_start

    # 4. 평가용 / Generator용 구분
    evaluation_docs = reranked_results
    generator_docs = reranked_results[:generator_top_k]

    total_elapsed = time.perf_counter() - total_start

    # 5. 평가 결과 출력
    print("\n" + "=" * 70)
    print(f"평가용 리랭킹 결과: {len(evaluation_docs)}개")
    print("=" * 70)

    for doc in evaluation_docs:

        metadata = doc.get("metadata") or doc.get("payload") or {}

        print(f"\n[Rerank {doc['rerank_rank']}위]")
        print(f"RRF 순위: {doc['rrf_rank']}")
        print(f"CE 순위: {doc['ce_rank']}")
        print(f"RRF 점수: {doc['rrf_score']}")
        print(f"CE 점수: {doc['rerank_score']:.6f}")
        print(f"최종 결합 점수: {doc['final_score']:.6f}")
        print(f"Point ID: {doc.get('point_id')}")
        print(f"Chunk ID: {metadata.get('chunk_id')}")
        print(f"Document ID: {metadata.get('document_id')}")
        print(f"본문:\n{doc.get('text', '')}")
        print("-" * 70)

    # 6. Generator 전달 대상
    print("\n" + "=" * 70)
    print(f"Generator 전달 대상: {len(generator_docs)}개")
    print("=" * 70)

    for doc in generator_docs:
        print(
            f"{doc['rerank_rank']}위 | "
            f"Point ID: {doc.get('point_id')} | "
            f"최종 점수: {doc['final_score']:.6f}"
        )

    # 7. 실행 시간
    print(f"\n리랭킹 소요 시간: {rerank_elapsed:.2f}초")
    print(f"전체 소요 시간: {total_elapsed:.2f}초")

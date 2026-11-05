"""Hybrid Search → RRF → Cross-Encoder Rerank 통합 테스트"""

import time

from src.rag.hybrid_search import hybrid_search
from src.rag.rrf import rrf
from src.rag.rerank import rerank, MODEL_NAME


# ==================================================
# 테스트 설정
# ==================================================

QUERY = "온라인 쇼핑몰에서 구매한 상품을 환불받을 수 있나요?"

SEARCH_TOP_K = 20
RERANK_TOP_K = 10
GENERATOR_TOP_K = 5


# ==================================================
# 테스트 실행
# ==================================================

if __name__ == "__main__":

    print("=" * 70)
    print("Cross-Encoder Reranker 통합 테스트")
    print(f"모델: {MODEL_NAME}")
    print(f"질문: {QUERY}")
    print("=" * 70)

    total_start = time.perf_counter()

    # 1. Hybrid Search
    print("\n[1] Qdrant Hybrid Search")

    search_results = hybrid_search(
        query=QUERY,
        top_k=SEARCH_TOP_K,
    )

    rankings = {
        "q1_dense": search_results["dense_results"],
        "q1_bm25": search_results["bm25_results"],
    }

    print(f"Dense: {len(rankings['q1_dense'])}개")
    print(f"BM25: {len(rankings['q1_bm25'])}개")

    assert rankings["q1_dense"] or rankings["q1_bm25"], (
        "검색 결과가 없습니다."
    )

    # 2. RRF
    print("\n[2] RRF 통합")

    rrf_results = rrf({"rankings": rankings})["documents"]

    print(f"RRF 반환: {len(rrf_results)}개")

    assert rrf_results, "RRF 결과가 없습니다."

    # 3. Cross-Encoder Rerank
    print("\n[3] Cross-Encoder 리랭킹")

    rerank_start = time.perf_counter()

    reranked_results = rerank(
        query=QUERY,
        documents=rrf_results,
        top_k=RERANK_TOP_K,
        batch_size=8,
    )

    rerank_elapsed = time.perf_counter() - rerank_start

    # 4. 결과 검증
    print("\n[4] 리랭킹 결과 검증")

    assert reranked_results, "리랭킹 결과가 없습니다."

    assert len(reranked_results) <= RERANK_TOP_K, (
        "리랭킹 반환 개수가 설정값을 초과했습니다."
    )

    assert all(
        reranked_results[i]["rerank_score"]
        >= reranked_results[i + 1]["rerank_score"]
        for i in range(len(reranked_results) - 1)
    ), "리랭킹 점수 내림차순 정렬에 실패했습니다."

    rrf_ids = {doc["point_id"] for doc in rrf_results}
    rerank_ids = {doc["point_id"] for doc in reranked_results}

    assert rerank_ids.issubset(rrf_ids), (
        "RRF에 없는 문서가 리랭킹 결과에 포함되었습니다."
    )

    assert all(
        doc["rerank_rank"] == index
        for index, doc in enumerate(reranked_results, start=1)
    ), "리랭킹 순위 기록이 올바르지 않습니다."

    assert all(
        1 <= doc["rrf_rank"] <= len(rrf_results)
        for doc in reranked_results
    ), "RRF 순위 기록이 올바르지 않습니다."

    print("리랭킹 반환 개수: PASS")
    print("점수 내림차순 정렬: PASS")
    print("RRF 문서 유지: PASS")
    print("리랭킹 순위 기록: PASS")
    print("RRF 순위 기록: PASS")

    # 5. RRF vs Rerank 순위 비교
    print("\n[5] RRF vs Rerank 순위 비교")
    print("-" * 85)
    print(
        f"{'Rerank':<10}"
        f"{'RRF':<10}"
        f"{'Rerank Score':<16}"
        f"{'Point ID'}"
    )
    print("-" * 85)

    for doc in reranked_results:
        print(
            f"{doc['rerank_rank']:<10}"
            f"{doc['rrf_rank']:<10}"
            f"{doc['rerank_score']:<16.6f}"
            f"{doc['point_id']}"
        )

    # 6. Generator 전달 대상
    print("\n[6] Generator 전달 대상")

    generator_docs = reranked_results[:GENERATOR_TOP_K]

    for doc in generator_docs:
        metadata = doc.get("metadata") or {}

        print(f"\n[Rerank {doc['rerank_rank']}위]")
        print(f"Rerank Score: {doc['rerank_score']:.6f}")
        print(f"Point ID: {doc['point_id']}")
        print(f"Document ID: {metadata.get('document_id')}")
        print(f"Chunk ID: {metadata.get('chunk_id')}")
        print(f"본문:\n{doc['text'][:300]}")
        print("-" * 70)

    # 7. 최종 결과
    total_elapsed = time.perf_counter() - total_start

    print("\n" + "=" * 70)
    print("테스트 통과: PASS")
    print("=" * 70)

    print(f"RRF 반환: {len(rrf_results)}개")
    print(f"Rerank 반환: {len(reranked_results)}개")
    print(f"Generator 전달: {len(generator_docs)}개")
    print(f"리랭킹 소요 시간: {rerank_elapsed:.2f}초")
    print(f"전체 소요 시간: {total_elapsed:.2f}초")
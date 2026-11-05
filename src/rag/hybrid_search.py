
import os
from functools import lru_cache

from dotenv import load_dotenv
from fastembed import SparseTextEmbedding
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient

from src.rag.dense_search import dense_search
from src.rag.bm25_search import bm25_search

load_dotenv(override=False)


@lru_cache(maxsize=1)
def _search_resources():
    client = QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        timeout=int(os.getenv("QDRANT_TIMEOUT_SECONDS", "10")),
    )
    dense_embedder = OpenAIEmbeddings(
        model=os.getenv(
            "OPENAI_EMBEDDING_MODEL",
            os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        ),
    )
    sparse_embedder = SparseTextEmbedding(
        model_name=os.getenv("SPARSE_EMBEDDING_MODEL", "Qdrant/bm25"),
        disable_stemmer=True,
    )
    return client, dense_embedder, sparse_embedder


# 2. Hybrid Search
def hybrid_search(
    query: str,
    top_k: int = 40,
    document_type: str | None = "law",
    domain: str | None = None,
) -> dict:
    """Dense와 BM25 검색을 각각 수행한다. RRF는 수행하지 않는다.

    document_type과 domain은 선택적 메타데이터 필터이다.
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("검색 질의는 비어 있지 않은 문자열이어야 합니다.")

    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k는 1 이상의 정수여야 합니다.")

    query = query.strip()
    client, dense_embedder, sparse_embedder = _search_resources()

    dense_results = dense_search(
        query,
        client,
        dense_embedder,
        top_k,
        document_type=document_type,
        domain=domain,
    )

    bm25_results = bm25_search(
        query,
        client,
        sparse_embedder,
        top_k,
        document_type=document_type,
        domain=domain,
    )

    return {
        "query": query,
        "dense_results": dense_results,
        "bm25_results": bm25_results,
    }


if __name__ == "__main__":
    try:
        question = input("검색할 질문을 입력하세요: ").strip()

        document_type = input(
            "문서 유형 필터 (전체 검색은 Enter): "
        ).strip() or None

        domain = input(
            "법률 분야 필터 (전체 검색은 Enter): "
        ).strip() or None

        results = hybrid_search(
            query=question,
            document_type=document_type,
            domain=domain,
        )

        print("\n[검색 조건]")
        print("질문:", results["query"])
        print("문서 유형:", document_type or "전체")
        print("법률 분야:", domain or "전체")

        print("\nDense 검색 결과:", len(results["dense_results"]))
        print("BM25 검색 결과:", len(results["bm25_results"]))

        print("\n[Dense 검색 결과]")
        for i, result in enumerate(results["dense_results"][:5], start=1):
            print(f"\n{i}위 | Score: {result['score']:.4f}")
            print("Point ID:", result["point_id"])
            print("Chunk ID:", result["payload"].get("chunk_id"))
            print("문서:", result["payload"].get("source_name"))
            print("내용:", result["payload"].get("text", ""))

        print("\n[BM25 검색 결과]")
        for i, result in enumerate(results["bm25_results"][:5], start=1):
            print(f"\n{i}위 | Score: {result['score']:.4f}")
            print("Point ID:", result["point_id"])
            print("Chunk ID:", result["payload"].get("chunk_id"))
            print("문서:", result["payload"].get("source_name"))
            print("내용:", result["payload"].get("text", ""))

    finally:
        if _search_resources.cache_info().currsize:
            client, _, _ = _search_resources()
            client.close()
            _search_resources.cache_clear()

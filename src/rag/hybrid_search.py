import os

from dotenv import load_dotenv
from fastembed import SparseTextEmbedding
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient

from src.rag.dense_search import dense_search
from src.rag.bm25_search import bm25_search

load_dotenv()


# 1. Qdrant 연결 및 임베딩 모델 생성 (한 번만 실행)
client = QdrantClient(
    url=os.environ["QDRANT_URL"],
    api_key=os.environ["QDRANT_API_KEY"],
    timeout=int(os.getenv("QDRANT_TIMEOUT_SECONDS", "10")),
)

dense_embedder = OpenAIEmbeddings(
    model=os.environ["EMBEDDING_MODEL"],
)

sparse_embedder = SparseTextEmbedding(
    model_name="Qdrant/bm25",
    disable_stemmer=True,
)


# 2. Hybrid Search
def hybrid_search(query: str, top_k: int = 20) -> dict:
    """Dense와 BM25 검색을 각각 수행한다. RRF는 수행하지 않는다."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("검색 질의는 비어 있지 않은 문자열이어야 합니다.")

    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k는 1 이상의 정수여야 합니다.")

    query = query.strip()

    dense_results = dense_search(
        query, client, dense_embedder, top_k
    )

    bm25_results = bm25_search(
        query, client, sparse_embedder, top_k
    )

    return {
        "query": query,
        "dense_results": dense_results,
        "bm25_results": bm25_results,
    }


if __name__ == "__main__":
    try:
        question = input("검색할 질문을 입력하세요: ").strip()

        results = hybrid_search(question)

        print("Dense 검색 결과:", len(results["dense_results"]))
        print("BM25 검색 결과:", len(results["bm25_results"]))


        print("\n[Dense 검색 결과]")
        for i, result in enumerate(results["dense_results"][:3], start=1):
            print(f"\n{i}위 | Score: {result['score']:.4f}")
            print("문서:", result["payload"].get("source_name"))
            print("내용:", result["payload"].get("text", "")[:200])

        print("\n[BM25 검색 결과]")
        for i, result in enumerate(results["bm25_results"][:3], start=1):
            print(f"\n{i}위 | Score: {result['score']:.4f}")
            print("문서:", result["payload"].get("source_name"))
            print("내용:", result["payload"].get("text", "")[:200])

    finally:
        client.close()
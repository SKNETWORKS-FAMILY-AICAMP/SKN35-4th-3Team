from qdrant_client import models


def hybrid_search(
    query: str,
    client,
    collection_name: str,
    dense_embedder,
    sparse_embedder,
    *,
    dense_name: str = "dense",
    sparse_name: str = "sparse",
    top_k: int = 20,
    candidate_k: int = 60,
    filters: models.Filter | None = None,
) -> list[dict]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("질문은 비어 있지 않은 문자열이어야 합니다.")

    for count in (top_k, candidate_k):
        if type(count) is not int or count < 1:
            raise ValueError("검색 개수는 양의 정수여야 합니다.")

    query = query.strip()
    candidate_k = max(candidate_k, top_k)

    dense_vector = dense_embedder.embed_query(query)
    sparse_vector = sparse_embedder.embed_query(query)

    result = client.query_points(
        collection_name=collection_name,
        prefetch=[
            models.Prefetch(
                query=dense_vector,
                using=dense_name,
                filter=filters,
                limit=candidate_k,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=list(sparse_vector.indices),
                    values=list(sparse_vector.values),
                ),
                using=sparse_name,
                filter=filters,
                limit=candidate_k,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=top_k,
        with_payload=True,
        with_vectors=False,
    )

    return [
        {
            "point_id": str(point.id),
            "score": point.score,
            "payload": point.payload or {},
        }
        for point in result.points
    ]



if __name__ == "__main__":
    import os

    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from langchain_qdrant import FastEmbedSparse
    from qdrant_client import QdrantClient

    from src.rag.multi_query import multi_query
    from src.rag.query_rewrite import query_rewrite

    load_dotenv()

    llm = ChatOpenAI(model=os.environ["OPENAI_MODEL"])

    client = QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.getenv("QDRANT_API_KEY") or None,
        timeout=30,
    )

    # 문서 색인에 사용한 모델·설정과 동일해야 합니다.
    dense_embedder = OpenAIEmbeddings(
        model=os.environ["EMBEDDING_MODEL"],
    )
    sparse_embedder = FastEmbedSparse(
        model_name=os.environ["SPARSE_MODEL"],
    )

    try:
        question = input("질문을 입력하세요: ").strip()
        rewritten = query_rewrite(question, llm)
        expanded_queries = multi_query(rewritten, llm)
        queries = list(dict.fromkeys([question, *expanded_queries]))

        rankings = {}

        for index, query in enumerate(queries):
            hits = hybrid_search(
                query,
                client,
                os.environ["QDRANT_COLLECTION"],
                dense_embedder,
                sparse_embedder,
                dense_name=os.environ["QDRANT_DENSE_NAME"],
                sparse_name=os.environ["QDRANT_SPARSE_NAME"],
                top_k=5,
            )
            rankings[f"query_{index}"] = hits

            print(f"\n[검색 질문 {index + 1}] {query}")
            for rank, hit in enumerate(hits, start=1):
                print(
                    f"{rank}. ID: {hit['point_id']} "
                    f"| RRF 점수: {hit['score']:.4f}"
                )
                print(hit["payload"])
    finally:
        client.close()
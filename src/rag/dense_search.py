import os

def dense_search(query, client, embedder, top_k=20):
    vector = embedder.embed_query(query)

    query_options = {}
    vector_name = os.getenv("QDRANT_VECTOR_NAME", "").strip()
    if vector_name:
        query_options["using"] = vector_name

    results = client.query_points(
        collection_name=os.getenv(
            "QDRANT_COLLECTION",
            os.getenv("QDRANT_COLLECTION_NAME", "team_documents_openai_dev"),
        ),
        query=vector,
        limit=top_k,
        with_payload=True,
        **query_options,
    ).points

    return [
        {
            "point_id": str(point.id),
            "score": point.score,
            "payload": point.payload or {},
        }
        for point in results
    ]

import os

def dense_search(query, client, embedder, top_k=20):
    vector = embedder.embed_query(query)

    results = client.query_points(
        collection_name=os.environ["QDRANT_COLLECTION"],
        query=vector,
        limit=top_k,
        with_payload=True,
    ).points

    return [
        {
            "point_id": str(point.id),
            "score": point.score,
            "payload": point.payload or {},
        }
        for point in results
    ]
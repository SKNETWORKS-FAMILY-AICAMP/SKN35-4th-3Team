import os
from qdrant_client import models

def bm25_search(query, client, embedder, top_k=20):
    vector = next(embedder.query_embed(query))

    results = client.query_points(
        collection_name=os.environ["QDRANT_COLLECTION"],
        query=models.SparseVector(
            indices=list(vector.indices),
            values=list(vector.values),
        ),
        using="bm25",
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
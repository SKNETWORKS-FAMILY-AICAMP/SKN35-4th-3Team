import os
from qdrant_client import models


def dense_search(
    query,
    client,
    embedder,
    top_k=40,
    document_type=None,
    domain=None,
):
    vector = embedder.embed_query(query)

    query_options = {}
    vector_name = os.getenv("QDRANT_VECTOR_NAME", "").strip()

    if vector_name:
        query_options["using"] = vector_name

    # 필요한 경우에만 메타데이터 필터 적용
    conditions = []

    if document_type:
        conditions.append(
            models.FieldCondition(
                key="document_type",
                match=models.MatchValue(value=document_type),
            )
        )

    if domain:
        conditions.append(
            models.FieldCondition(
                key="domains",
                match=models.MatchValue(value=domain),
            )
        )

    query_filter = (
        models.Filter(must=conditions)
        if conditions
        else None
    )

    results = client.query_points(
        collection_name=os.getenv(
            "QDRANT_COLLECTION",
            os.getenv(
                "QDRANT_COLLECTION_NAME",
                "team_documents_openai_dev",
            ),
        ),
        query=vector,
        query_filter=query_filter,
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

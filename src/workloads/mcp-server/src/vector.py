"""
Qdrant vector store client for policy retrieval.

Uses the remote dense-embedder service for embeddings — no local model.
Trace context is NOT propagated to Qdrant or dense for search calls.
Child spans are created in tools.py for observability.
"""

from __future__ import annotations

from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient

from config import settings

_client = AsyncQdrantClient(url=settings.qdrant_url)
_http = httpx.AsyncClient(timeout=30.0)


async def _embed_query(text: str) -> list[float]:
    resp = await _http.post(
        f"{settings.dense_url}/embed",
        json={"texts": [text]},
    )
    resp.raise_for_status()
    vectors = resp.json().get("vectors", [])
    if not vectors:
        raise RuntimeError("dense service returned empty vectors")
    return [float(x) for x in vectors[0]]


async def hybrid_search(
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    vector = await _embed_query(query)
    results = await _client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "text": point.payload.get("text", ""),
            "score": point.score,
            "metadata": {k: v for k, v in point.payload.items() if k != "text"},
        }
        for point in results.points
    ]
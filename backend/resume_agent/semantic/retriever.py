"""Nearest-neighbor retrieval over the Qdrant fact collection.

The similarity search itself (ranking, top-k selection) now happens inside
Qdrant, not in this file — `index.py` configures the collection with
COSINE distance, so `query_points` returns hits already ranked by cosine
similarity. This module's job is just: embed the query, ask Qdrant for
neighbors, translate hits back into `SemanticMatch` objects.

Cosine, not Euclidean distance: we care whether two texts point in the
same *direction* in embedding space (same meaning), not how long the
vectors are. Embedding magnitude is mostly an artifact of text
length/model internals, not meaning, so a distance metric sensitive to
magnitude would penalize a short JD line against a longer fact summary
for no semantic reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chunker import FactChunk
from .embedder import embed_texts
from .index import FactIndex, chunk_from_payload, load_or_build_index


@dataclass(frozen=True)
class SemanticMatch:
    chunk: FactChunk
    score: float


def semantic_search(query: str, index: FactIndex | None = None, top_k: int = 5) -> list[SemanticMatch]:
    if index is None:
        index = load_or_build_index()
    query_vec = embed_texts([query])[0]
    result = index.client.query_points(
        collection_name=index.collection_name,
        query=query_vec.tolist(),
        limit=top_k,
    )
    return [
        SemanticMatch(chunk=chunk_from_payload(point.payload), score=float(point.score))
        for point in result.points
    ]


if __name__ == "__main__":
    for match in semantic_search("熟悉大模型的上下文工程与工具调用"):
        print(f"{match.score:.3f}  {match.chunk.fact_id}  {match.chunk.title}")

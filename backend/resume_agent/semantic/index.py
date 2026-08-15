"""Store and query fact-chunk embeddings in a real local vector database.

Engine: Qdrant in embedded/local mode (`QdrantClient(path=...)`) — a
persistent on-disk collection, no server process. This replaced an earlier
JSON-file cache that did the same job with a hand-rolled format; the reason
to swap was that "vector database experience" should mean operating an
actual vector database engine (collections, distance metrics, upserts,
nearest-neighbor queries), not a lookup table that happens to store lists
of floats. `fastembed` (already in use for embeddings) is Qdrant's own
library, so this is a natural pairing, not a new vendor to learn.

Distance metric: COSINE, configured on the collection itself, so Qdrant
computes and ranks by cosine similarity server-side (see retriever.py for
why cosine is the right metric here). `hit.score` on a query result is
already that cosine similarity — no separate similarity math needed.

Staleness handling: a sidecar stores the source hash plus embedding model,
pooling behavior, and dependency versions. Any change rebuilds the local
collection instead of silently serving incompatible vectors.

Scale note: still ~6 facts. A single local collection is the right size
for that; sharding/replication/HNSW tuning are not relevant questions at
this corpus size, and claiming otherwise would be the same overclaim this
project exists to prevent.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from .chunker import FACTS_PATH, FactChunk, load_fact_chunks
from .embedder import MODEL_NAME, POOLING_BEHAVIOR, embed_texts

QDRANT_PATH = Path(__file__).resolve().parents[3] / "data" / "semantic_index" / "qdrant_data"
META_PATH = Path(__file__).resolve().parents[3] / "data" / "semantic_index" / "qdrant_meta.json"
COLLECTION_NAME = "facts"


@dataclass(frozen=True)
class FactIndex:
    client: QdrantClient
    collection_name: str
    chunks: list[FactChunk]


def _facts_hash() -> str:
    return hashlib.sha256(FACTS_PATH.read_bytes()).hexdigest()


def _expected_meta() -> dict[str, str]:
    return {
        "facts_hash": _facts_hash(),
        "embedding_model": MODEL_NAME,
        "pooling_behavior": POOLING_BEHAVIOR,
        "fastembed_version": version("fastembed"),
        "qdrant_client_version": version("qdrant-client"),
    }


def _payload(chunk: FactChunk) -> dict:
    return {
        "fact_id": chunk.fact_id,
        "title": chunk.title,
        "text": chunk.text,
        "keywords": chunk.keywords,
        "risk": chunk.risk,
    }


def chunk_from_payload(payload: dict) -> FactChunk:
    return FactChunk(
        fact_id=payload["fact_id"],
        title=payload["title"],
        text=payload["text"],
        keywords=payload["keywords"],
        risk=payload["risk"],
    )


def build_index(client: QdrantClient | None = None, path: Path = QDRANT_PATH) -> FactIndex:
    client = client or QdrantClient(path=str(path))

    chunks = load_fact_chunks()
    vectors = embed_texts([chunk.text for chunk in chunks])

    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=int(vectors.shape[1]), distance=Distance.COSINE),
    )
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(id=i, vector=vectors[i].tolist(), payload=_payload(chunk))
            for i, chunk in enumerate(chunks)
        ],
    )

    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(_expected_meta(), indent=2), encoding="utf-8")

    return FactIndex(client=client, collection_name=COLLECTION_NAME, chunks=chunks)


def _cached_meta_matches() -> bool:
    if not META_PATH.exists():
        return False
    try:
        stored = json.loads(META_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return stored == _expected_meta()


def load_or_build_index(path: Path = QDRANT_PATH, force: bool = False) -> FactIndex:
    client = QdrantClient(path=str(path))

    if not force and _cached_meta_matches() and client.collection_exists(COLLECTION_NAME):
        points, _ = client.scroll(collection_name=COLLECTION_NAME, limit=10_000, with_payload=True)
        points.sort(key=lambda p: p.id)
        chunks = [chunk_from_payload(p.payload) for p in points]
        return FactIndex(client=client, collection_name=COLLECTION_NAME, chunks=chunks)

    return build_index(client=client, path=path)


if __name__ == "__main__":
    idx = load_or_build_index(force=True)
    print(f"Indexed {len(idx.chunks)} chunks into Qdrant collection '{idx.collection_name}' at {QDRANT_PATH}")

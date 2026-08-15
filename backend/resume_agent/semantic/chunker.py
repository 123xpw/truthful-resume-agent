"""Turn fact-bank records into embeddable text chunks.

Loads the same resolved fact source as the CLI: private `facts.json` when
present, otherwise the public `facts.example.json` fixture.

Chunking strategy: one chunk per fact record, no sub-splitting.
The fact bank is already atomic (~6 short, single-topic records), so
splitting further would only throw away context that embeddings need.
Chunking strategy should match corpus granularity, not follow a generic
"always split long documents" recipe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..fact_store import resolve_facts_path


FACTS_PATH = resolve_facts_path()


@dataclass(frozen=True)
class FactChunk:
    fact_id: str
    title: str
    text: str
    keywords: list[str]
    risk: str


def _chunk_text(title: str, summary: str) -> str:
    # Natural-language sentence, not a keyword list: the embedding model
    # was trained on sentences, so this reads closer to what it saw
    # during training than a bag of tags would.
    return f"{title}. {summary}"


def load_fact_chunks(path: Path = FACTS_PATH) -> list[FactChunk]:
    from ..fact_store import load_facts

    facts = load_facts(resolve_facts_path(path))
    chunks: list[FactChunk] = []
    for fact in facts:
        chunks.append(
            FactChunk(
                fact_id=fact.id,
                title=fact.title,
                text=_chunk_text(fact.title, fact.summary),
                keywords=list(fact.keywords),
                risk=fact.risk,
            )
        )
    return chunks


if __name__ == "__main__":
    for chunk in load_fact_chunks():
        print(chunk.fact_id, "->", chunk.text)

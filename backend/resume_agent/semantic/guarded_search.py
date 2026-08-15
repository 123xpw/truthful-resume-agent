"""semantic_search() with the two guardrails jd_eval.py showed were needed.

Plain semantic_search() ranks by topical closeness only. Two failure
modes follow from that, and each needs a different fix:

1. Low-signal queries (e.g. a soft-skills line) still return a top-k list,
   just with lower scores -- MIN_SEMANTIC_SCORE drops those.
2. Queries naming tech the fact bank has no evidence for can still score
   in the same range as genuine matches, because "topically related" and
   "evidenced" are different things a cosine score cannot tell apart --
   find_blocked_terms() vetoes those regardless of score.

Guardrail 2 wins if both would otherwise apply: an unevidenced-tech query
is not writable no matter how high any match scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..rules import Fact
from .guardrails import MIN_SEMANTIC_SCORE, find_blocked_terms
from .index import FactIndex
from .retriever import SemanticMatch, semantic_search


@dataclass(frozen=True)
class GuardedResult:
    matches: list[SemanticMatch]
    blocked_terms: list[tuple[str, str]]

    @property
    def is_writable(self) -> bool:
        return not self.blocked_terms and bool(self.matches)


def guarded_semantic_search(
    query: str,
    index: FactIndex | None = None,
    top_k: int = 5,
    min_score: float = MIN_SEMANTIC_SCORE,
    facts: Iterable[Fact] | None = None,
) -> GuardedResult:
    blocked_terms = find_blocked_terms(query, facts=facts)
    if blocked_terms:
        return GuardedResult(matches=[], blocked_terms=blocked_terms)

    matches = [m for m in semantic_search(query, index=index, top_k=top_k) if m.score >= min_score]
    return GuardedResult(matches=matches, blocked_terms=[])

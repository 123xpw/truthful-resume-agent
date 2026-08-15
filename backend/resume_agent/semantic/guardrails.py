"""Block terms the fact bank has no real evidence for, regardless of score.

jd_eval.py surfaced a concrete failure: the query "了解vLLM、Ollama等推理框架
原理...KV cache优化" scored 0.515 against intern_optimization_ai_coding --
inside the same range as genuine hits (0.43-0.73) -- even though the fact
bank has zero AI-infra evidence. A similarity threshold cannot separate
that case from a real match; the score reflects topical closeness
("AI-adjacent"), not evidence. The only reliable fix is the same one
analyzer.py uses on the keyword side: a blocklist of terms that must not
be presented as writable unless the fact bank actually backs them.

The blocked terms and the evidence check both come from rules.py
(NOT_WRITABLE_TECH, has_fact_evidence), so keyword and semantic matching
share one truthfulness boundary before integration -- and both sides
un-block a term the same way: add a fact with real evidence for it.
"""

from __future__ import annotations

from typing import Iterable

from ..fact_store import load_facts
from ..rules import Fact, NOT_WRITABLE_TECH, has_fact_evidence
from ..rules import term_matches as _contains
from .thresholds import GUARDRAIL_MIN_SEMANTIC_SCORE

# Below this, jd_eval.py's real-JD run only ever produced noise (soft-skill
# lines scored 0.21-0.23; every line with a plausible topical link scored
# >=0.38). Not a precision guarantee -- see the vLLM case above -- just a
# floor that drops the clearest non-matches.
MIN_SEMANTIC_SCORE = GUARDRAIL_MIN_SEMANTIC_SCORE


def find_blocked_terms(text: str, facts: Iterable[Fact] | None = None) -> list[tuple[str, str]]:
    facts = list(facts) if facts is not None else list(load_facts())
    return [
        (term, reason)
        for term, reason in NOT_WRITABLE_TECH.items()
        if _contains(text, term) and not has_fact_evidence(term, facts)
    ]

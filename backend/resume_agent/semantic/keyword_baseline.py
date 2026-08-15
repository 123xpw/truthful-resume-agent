"""Minimal, self-contained keyword matcher, for comparison only.

This intentionally duplicates (not imports) the same substring/word-
boundary matching idea already implemented in analyzer.py. Importing
analyzer.py would couple this module to a file someone else is actively
editing in parallel (Goal A); a small local copy keeps semantic/ fully
independent. It does not need to be the same implementation, only good
enough to demonstrate the real gap keyword matching has: it can only
find what is spelled out. It cannot find synonyms, translations, or
paraphrases the fact bank happens to use different words for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .chunker import FactChunk, load_fact_chunks


@dataclass(frozen=True)
class KeywordMatch:
    chunk: FactChunk
    matched_keywords: list[str]


def _contains_keyword(text: str, keyword: str) -> bool:
    if re.search(r"[一-鿿]", keyword):
        return keyword in text
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(keyword.lower())}(?![A-Za-z0-9_])")
    return bool(pattern.search(text.lower()))


def keyword_search(jd_text: str, chunks: list[FactChunk] | None = None) -> list[KeywordMatch]:
    if chunks is None:
        chunks = load_fact_chunks()
    matches: list[KeywordMatch] = []
    for chunk in chunks:
        hit = [kw for kw in chunk.keywords if _contains_keyword(jd_text, kw)]
        if hit:
            matches.append(KeywordMatch(chunk=chunk, matched_keywords=hit))
    matches.sort(key=lambda m: len(m.matched_keywords), reverse=True)
    return matches

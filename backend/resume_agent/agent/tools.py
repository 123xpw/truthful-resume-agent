"""Agent 工具：复用现有事实库的检索与溯源校验能力。"""

from __future__ import annotations

import json
from typing import Iterable

from langchain_core.tools import tool

from ..analyzer import contains_keyword
from ..fact_store import load_facts
from ..rules import Fact


def build_search_payload(query: str, facts: Iterable[Fact], *, limit: int = 5) -> dict:
    """Build the deterministic lexical evidence bundle used by the Agent.

    Keeping this logic outside the decorated tool gives evaluations a way to
    compare the production retrieval path without invoking LangChain or
    reading a different fact-bank snapshot.
    """
    normalized_query = query.lower()
    tokens = normalized_query.split()
    scored = []
    for fact in facts:
        haystack = f"{fact.id} {fact.title} {fact.summary} {' '.join(fact.keywords)}".lower()
        keyword_hits = sum(1 for keyword in fact.keywords if keyword.lower() in normalized_query)
        token_hits = sum(1 for token in tokens if len(token) >= 2 and contains_keyword(haystack, token))
        score = keyword_hits * 3 + token_hits
        # A registered keyword is strong enough by itself. Free query tokens
        # need at least two boundary-aware hits so generic words such as
        # "implementation" do not surface unrelated facts; this also prevents
        # substring errors such as production -> reproduction.
        if keyword_hits > 0 or token_hits >= 2:
            scored.append((score, fact))
    scored.sort(key=lambda item: -item[0])
    payload = {
        "query": query,
        "matches": [
            {
                "fact_id": fact.id,
                "title": fact.title,
                "summary": fact.summary,
                "boundaries": list(fact.boundaries),
                "risk": fact.risk,
                "lexical_score": score,
            }
            for score, fact in scored[: max(0, limit)]
        ],
    }
    if not scored:
        payload["message"] = "事实库中没有与查询相关的事实。"
    return payload


@tool
def search_facts(query: str) -> str:
    """检索事实库，返回机器可读的事实证据包。"""
    payload = build_search_payload(query, load_facts())
    return json.dumps(payload, ensure_ascii=False)


@tool
def verify_fact(fact_id: str, claim: str = "") -> str:
    """返回校验一条具体 claim 所需的完整事实与边界；不替代最终语义判断。"""
    for fact in load_facts():
        if fact.id == fact_id:
            return json.dumps(
                {
                    "exists": True,
                    "fact_id": fact.id,
                    "claim": claim,
                    "title": fact.title,
                    "summary": fact.summary,
                    "boundaries": list(fact.boundaries),
                    "risk": fact.risk,
                },
                ensure_ascii=False,
            )
    return json.dumps(
        {"exists": False, "fact_id": fact_id, "claim": claim},
        ensure_ascii=False,
    )

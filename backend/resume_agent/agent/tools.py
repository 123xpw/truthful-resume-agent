"""Agent 工具：复用现有事实库的检索与溯源校验能力。"""

from __future__ import annotations

from langchain_core.tools import tool

from ..fact_store import load_facts


@tool
def search_facts(query: str) -> str:
    """检索事实库，返回与 query 相关的事实（编号 + 标题 + 摘要 + 边界）。"""
    facts = load_facts()
    tokens = query.lower().split()
    scored = []
    for fact in facts:
        haystack = f"{fact.summary} {' '.join(fact.keywords)}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if score > 0:
            scored.append((score, fact))
    scored.sort(key=lambda item: -item[0])
    if not scored:
        return "事实库中没有与查询相关的事实。"
    lines = []
    for score, fact in scored[:5]:
        lines.append(f"- [{fact.id}] {fact.title}")
        lines.append(f"  摘要: {fact.summary[:160]}")
        boundaries = "; ".join(fact.boundaries)[:200]
        lines.append(f"  边界: {boundaries}")
    return "\n".join(lines)


@tool
def verify_fact(fact_id: str) -> str:
    """校验某个事实编号是否存在于事实库，返回其边界与风险等级。"""
    for fact in load_facts():
        if fact.id == fact_id:
            boundaries = "; ".join(fact.boundaries)
            return f"事实 {fact_id} 存在。风险等级: {fact.risk}。边界: {boundaries}"
    return f"事实 {fact_id} 不存在于事实库。"

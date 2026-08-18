"""Lightweight RAG retrieval evaluation.

Measures whether the fact bank surfaces the expected facts for a small set of
held-out queries. Metrics: Recall@K and Mean Reciprocal Rank (MRR).

Usage:
    python -m backend.resume_agent.rag_eval
"""

from __future__ import annotations

from pathlib import Path

from .fact_store import load_facts
from .rules import Fact

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# (query, expected_fact_ids) — minimal held-out eval set.
EVAL_QUERIES: list[tuple[str, set[str]]] = [
    ("RAG 向量检索 语义召回 Qdrant", {"project_truthful_resume_agent_rag_qdrant"}),
    ("Python REST API 金融数据抓取自动化", {"intern_data_automation"}),
    ("Claude Code AI 编程工具辅助复杂代码库", {"intern_optimization_ai_coding"}),
    ("多模态生成 CLIP 图文对齐评测", {"project_emotion_pixel_eval"}),
    ("求解器集成 优化 LP 后端", {"intern_solver_integration_clarabel"}),
]


def _keyword_score(fact: Fact, query: str) -> int:
    haystack = f"{fact.summary} {' '.join(fact.keywords)}".lower()
    return sum(1 for token in query.lower().split() if token in haystack)


def evaluate_retrieval(facts: list[Fact], k: int = 5) -> dict:
    by_id = {fact.id: fact for fact in facts}
    per_query = []
    hit_count = 0
    reciprocal_ranks = []
    for query, expected in EVAL_QUERIES:
        scored = [
            (fact_id, _keyword_score(by_id[fact_id], query))
            for fact_id in by_id
        ]
        scored = [(fid, score) for fid, score in scored if score > 0]
        scored.sort(key=lambda item: item[1], reverse=True)
        ranked = [fid for fid, _ in scored]
        top_k = ranked[:k]
        hit = bool(expected & set(top_k))
        if hit:
            hit_count += 1
        rank = next((i + 1 for i, fid in enumerate(ranked) if fid in expected), 0)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
        per_query.append({
            "query": query,
            "expected": sorted(expected),
            "top_k": top_k,
            "hit": hit,
            "reciprocal_rank": round(1 / rank, 4) if rank else 0.0,
        })
    total = len(EVAL_QUERIES)
    return {
        "recall_at_k": round(hit_count / total, 4) if total else 0.0,
        "mrr": round(sum(reciprocal_ranks) / total, 4) if total else 0.0,
        "per_query": per_query,
    }


def render_report(result: dict) -> str:
    k = len(result["per_query"][0]["top_k"]) if result["per_query"] else 0
    lines = ["# RAG Retrieval Evaluation", ""]
    lines.append(f"- Recall@{k}: {result['recall_at_k']}")
    lines.append(f"- MRR: {result['mrr']}")
    lines.append("")
    for item in result["per_query"]:
        mark = "PASS" if item["hit"] else "MISS"
        lines.append(f"- [{mark}] {item['query']}")
        lines.append(f"  - expected: {', '.join(item['expected'])}")
        lines.append(f"  - top_k: {', '.join(item['top_k'])}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    facts = load_facts(PROJECT_ROOT / "data" / "facts" / "facts.json")
    result = evaluate_retrieval(facts)
    print(render_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Compare keyword baseline with the actual Qdrant semantic retrieval path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from .semantic.index import load_or_build_index
from .semantic.keyword_baseline import keyword_search
from .semantic.retriever import semantic_search


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "data" / "evaluation" / "retrieval_cases.json"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("retrieval eval file must contain a non-empty cases list")
    ids = [str(case.get("id", "")) for case in cases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("retrieval case IDs must be non-empty and unique")
    return cases


def _deduplicate_fact_ids(fact_ids: list[str], k: int) -> list[str]:
    return list(dict.fromkeys(fact_ids))[:k]


def _score_matcher(
    cases: list[dict],
    search: Callable[[str], list[str]],
    *,
    k: int,
) -> dict:
    per_case = []
    hits = 0
    reciprocal_ranks: list[float] = []
    for case in cases:
        ranked = _deduplicate_fact_ids(search(str(case["query"])), k)
        expected = set(str(item) for item in case["expected_fact_ids"])
        hit = bool(expected.intersection(ranked))
        rank = next((index + 1 for index, fact_id in enumerate(ranked) if fact_id in expected), 0)
        hits += int(hit)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
        per_case.append(
            {
                "id": case["id"],
                "query": case["query"],
                "expected": sorted(expected),
                "top_k": ranked,
                "hit": hit,
                "reciprocal_rank": round(1 / rank, 4) if rank else 0.0,
            }
        )
    count = len(cases)
    return {
        "recall_at_k": round(hits / count, 4) if count else 0.0,
        "mrr": round(sum(reciprocal_ranks) / count, 4) if count else 0.0,
        "per_case": per_case,
    }


def evaluate(cases: list[dict], k: int = 5) -> dict:
    index = load_or_build_index()

    def keyword(query: str) -> list[str]:
        return [match.chunk.fact_id for match in keyword_search(query)]

    def semantic(query: str) -> list[str]:
        # Fetch extra chunks before fact-level deduplication so one fact with
        # multiple chunks does not crowd other candidates out of Recall@K.
        matches = semantic_search(query, index=index, top_k=max(k * 4, k))
        return [match.chunk.fact_id for match in matches]

    return {
        "k": k,
        "case_count": len(cases),
        "keyword": _score_matcher(cases, keyword, k=k),
        "semantic_qdrant": _score_matcher(cases, semantic, k=k),
    }


def render(result: dict) -> str:
    k = result["k"]
    keyword = result["keyword"]
    semantic = result["semantic_qdrant"]
    lines = [
        "# Retrieval Evaluation",
        "",
        f"- Cases: {result['case_count']}",
        f"- Keyword Recall@{k}: {keyword['recall_at_k']}",
        f"- Keyword MRR: {keyword['mrr']}",
        f"- Qdrant semantic Recall@{k}: {semantic['recall_at_k']}",
        f"- Qdrant semantic MRR: {semantic['mrr']}",
        "",
    ]
    semantic_by_id = {item["id"]: item for item in semantic["per_case"]}
    for item in keyword["per_case"]:
        semantic_item = semantic_by_id[item["id"]]
        lines.append(
            f"- {item['id']}: keyword={'PASS' if item['hit'] else 'MISS'}, "
            f"qdrant={'PASS' if semantic_item['hit'] else 'MISS'}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--min-semantic-recall", type=float, default=0.9)
    parser.add_argument("--min-semantic-mrr", type=float, default=0.6)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be at least 1")
    result = evaluate(load_cases(args.cases), k=args.k)
    print(render(result))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    semantic = result["semantic_qdrant"]
    passed = (
        semantic["recall_at_k"] >= args.min_semantic_recall
        and semantic["mrr"] >= args.min_semantic_mrr
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

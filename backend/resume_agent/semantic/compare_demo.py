"""Side-by-side demo: keyword matching vs semantic retrieval.

The fact bank is written with English keywords (Prompt Engineering,
REST API, multimodal, ...); real JDs get pasted in Chinese and rarely
reuse those exact English tokens. The queries below are Chinese JD-style
phrasings of what each fact already covers, deliberately avoiding the
literal keyword substrings, so the keyword baseline's failure mode
(exact substring only) and the semantic retriever's strength (meaning
across language/wording) both show up on the same input.

Each query names the fact_id it paraphrases so a reader can check the
semantic result against ground truth without re-reading facts.json.
"""

from __future__ import annotations

from dataclasses import dataclass

from .index import load_or_build_index
from .keyword_baseline import keyword_search
from .retriever import semantic_search

QUERIES = [
    ("提示词优化与大语言模型上下文管理经验", "project_emotion_pixel_eval"),
    ("熟悉图像生成模型微调，如扩散模型相关方法", "project_dl_learning_lab"),
    ("有图文结合的多模态推荐场景开发经验", "intern_csharp_ai_mvp"),
    ("会用AI编程工具辅助复杂代码理解与文档撰写", "intern_optimization_ai_coding"),
    ("接口自动化取数与报表处理经验", "intern_data_automation"),
]


@dataclass(frozen=True)
class ComparisonRow:
    query: str
    expected_fact_id: str
    keyword_hit: bool
    semantic_hit: bool
    semantic_top_score: float


def run_comparison() -> list[ComparisonRow]:
    index = load_or_build_index()
    rows: list[ComparisonRow] = []
    for query, expected_id in QUERIES:
        kw_matches = keyword_search(query)
        kw_hit = any(m.chunk.fact_id == expected_id for m in kw_matches)

        sem_matches = semantic_search(query, index=index, top_k=1)
        sem_hit = bool(sem_matches) and sem_matches[0].chunk.fact_id == expected_id
        sem_score = sem_matches[0].score if sem_matches else 0.0

        rows.append(ComparisonRow(query, expected_id, kw_hit, sem_hit, sem_score))
    return rows


def _mark(hit: bool) -> str:
    return "✓" if hit else "✗"


def print_report(rows: list[ComparisonRow]) -> None:
    print(f"{'query':<40} {'expected':<32} {'keyword':<8} {'semantic':<10} score")
    print("-" * 100)
    for row in rows:
        print(
            f"{row.query:<40} {row.expected_fact_id:<32} "
            f"{_mark(row.keyword_hit):<8} {_mark(row.semantic_hit):<10} {row.semantic_top_score:.3f}"
        )
    kw_wins = sum(r.keyword_hit for r in rows)
    sem_wins = sum(r.semantic_hit for r in rows)
    print("-" * 100)
    print(f"keyword hits: {kw_wins}/{len(rows)}   semantic hits: {sem_wins}/{len(rows)}")


if __name__ == "__main__":
    print_report(run_comparison())

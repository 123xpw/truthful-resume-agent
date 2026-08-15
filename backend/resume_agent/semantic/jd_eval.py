"""Run keyword vs semantic retrieval over a real, unedited JD file.

compare_demo.py uses five hand-written queries with known expected
fact_ids -- useful for a controlled pass/fail number, but it is still
queries I wrote to make a point. This script instead takes a JD exactly
as pasted from a job posting, splits it into individual requirement
lines, and shows what each side finds. There is no ground-truth fact_id
per line here, so this does not print a hit/miss score -- it prints
what a reviewer would need to judge retrieval quality themselves.

Line splitting is intentionally simple (numbered/bulleted lines only)
and duplicated rather than imported from analyzer.py, for the same
parallel-editing reason keyword_baseline.py gives.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .guarded_search import guarded_semantic_search
from .index import load_or_build_index
from .keyword_baseline import keyword_search
from .retriever import semantic_search

LIST_LINE = re.compile(r"^\s*(?:[-*]|\d+[.、])\s*(.+)")


def extract_requirement_lines(jd_text: str) -> list[str]:
    lines: list[str] = []
    for raw in jd_text.splitlines():
        match = LIST_LINE.match(raw)
        if match:
            text = match.group(1).strip()
            if len(text) >= 6:
                lines.append(text)
    return lines


def print_line_report(line: str, index) -> None:
    kw_matches = keyword_search(line)
    sem_matches = semantic_search(line, index=index, top_k=2)

    print(f"- {line}")
    if kw_matches:
        kw_str = ", ".join(f"{m.chunk.fact_id}({'/'.join(m.matched_keywords)})" for m in kw_matches[:3])
        print(f"    keyword : {kw_str}")
    else:
        print("    keyword : (no match)")

    sem_str = ", ".join(f"{m.chunk.fact_id}={m.score:.3f}" for m in sem_matches)
    print(f"    semantic: {sem_str}")

    guarded = guarded_semantic_search(line, index=index)
    if guarded.blocked_terms:
        terms = ", ".join(term for term, _ in guarded.blocked_terms)
        print(f"    guarded : BLOCKED ({terms} not in fact bank)")
    elif guarded.matches:
        guarded_str = ", ".join(f"{m.chunk.fact_id}={m.score:.3f}" for m in guarded.matches)
        print(f"    guarded : {guarded_str}")
    else:
        print("    guarded : (below threshold, no output)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare keyword vs semantic retrieval on a real JD file.")
    parser.add_argument("path", type=Path, help="Path to a JD markdown file")
    args = parser.parse_args()

    jd_text = args.path.read_text(encoding="utf-8")
    lines = extract_requirement_lines(jd_text)
    if not lines:
        print("No numbered/bulleted requirement lines found in this file.")
        return 1

    index = load_or_build_index()
    print(f"{args.path.name}: {len(lines)} requirement lines\n")
    for line in lines:
        print_line_report(line, index)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

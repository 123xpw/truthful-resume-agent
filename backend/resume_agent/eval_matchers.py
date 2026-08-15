from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .analyzer import AnalysisResult, FactMatch, analyze_jd


@dataclass(frozen=True)
class MatcherEval:
    jd_path: Path
    keyword: AnalysisResult
    semantic: AnalysisResult


@dataclass(frozen=True)
class MatcherMetrics:
    selected_count: int
    useful_precision: float
    supported_precision: float
    useful_recall: float
    top3_supported_precision: float
    irrelevant_ids: tuple[str, ...]
    missed_useful_ids: tuple[str, ...]
    unrated_ids: tuple[str, ...]


def _fact_ids(matches: list[FactMatch]) -> list[str]:
    return [match.fact.id for match in matches]


def _match_label(match: FactMatch) -> str:
    evidence = ", ".join(match.matched_keywords)
    return f"{match.fact.id} ({evidence})"


def _format_matches(matches: list[FactMatch]) -> str:
    if not matches:
        return "None"
    return "<br>".join(_match_label(match) for match in matches)


def load_audit_labels(path: Path) -> tuple[str, dict[str, dict[str, dict[str, str]]]]:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    reviewer = str(raw.get("reviewer", "unknown_reviewer"))
    cases = raw.get("cases", {})
    if not isinstance(cases, dict):
        raise ValueError("matcher label file must contain an object named 'cases'")
    return reviewer, cases


def score_matches(matches: list[FactMatch], ratings: dict[str, dict[str, str]]) -> MatcherMetrics:
    selected_ids = [match.fact.id for match in matches]
    selected_labels = {fact_id: ratings.get(fact_id, {}).get("label") for fact_id in selected_ids}
    rated_ids = [
        fact_id
        for fact_id in selected_ids
        if selected_labels[fact_id] in {"useful", "marginal", "irrelevant"}
    ]
    useful_ids = {fact_id for fact_id, item in ratings.items() if item.get("label") == "useful"}
    useful_selected = sum(selected_labels[fact_id] == "useful" for fact_id in rated_ids)
    supported_selected = sum(selected_labels[fact_id] in {"useful", "marginal"} for fact_id in rated_ids)
    top3_rated = [
        fact_id
        for fact_id in selected_ids[:3]
        if selected_labels.get(fact_id) in {"useful", "marginal", "irrelevant"}
    ]
    top3_supported = sum(selected_labels[fact_id] in {"useful", "marginal"} for fact_id in top3_rated)
    return MatcherMetrics(
        selected_count=len(selected_ids),
        useful_precision=useful_selected / len(rated_ids) if rated_ids else 0.0,
        supported_precision=supported_selected / len(rated_ids) if rated_ids else 0.0,
        useful_recall=len(useful_ids.intersection(selected_ids)) / len(useful_ids) if useful_ids else 1.0,
        top3_supported_precision=top3_supported / len(top3_rated) if top3_rated else 0.0,
        irrelevant_ids=tuple(fact_id for fact_id in selected_ids if selected_labels.get(fact_id) == "irrelevant"),
        missed_useful_ids=tuple(sorted(useful_ids.difference(selected_ids))),
        unrated_ids=tuple(fact_id for fact_id in selected_ids if fact_id not in rated_ids),
    )


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _format_audited_matches(matches: list[FactMatch], ratings: dict[str, dict[str, str]]) -> str:
    if not matches:
        return "None"
    return "<br>".join(
        f"{_match_label(match)} [{ratings.get(match.fact.id, {}).get('label', 'unrated')}]"
        for match in matches
    )


def _format_terms(result: AnalysisResult) -> str:
    return ", ".join(result.not_writable) if result.not_writable else "None"


def _format_delta(keyword: AnalysisResult, semantic: AnalysisResult) -> str:
    keyword_ids = set(_fact_ids(keyword.strong_matches + keyword.weak_matches))
    semantic_ids = set(_fact_ids(semantic.strong_matches + semantic.weak_matches))
    added = sorted(semantic_ids - keyword_ids)
    removed = sorted(keyword_ids - semantic_ids)
    parts: list[str] = []
    if added:
        parts.append("semantic_only: " + ", ".join(added))
    if removed:
        parts.append("keyword_only: " + ", ".join(removed))
    return "<br>".join(parts) if parts else "same fact set"


def evaluate_jd(jd_path: Path) -> MatcherEval:
    jd_text = jd_path.read_text(encoding="utf-8")
    return MatcherEval(
        jd_path=jd_path,
        keyword=analyze_jd(jd_text, matcher="keyword"),
        semantic=analyze_jd(jd_text, matcher="semantic"),
    )


def render_markdown(
    evals: list[MatcherEval],
    label_cases: dict[str, dict[str, dict[str, str]]] | None = None,
    reviewer: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Matcher Evaluation")
    lines.append("")
    lines.append("Compares the default keyword matcher with the opt-in semantic matcher.")
    lines.append("This report is a review aid, not an automatic pass/fail judgment.")
    if label_cases is not None:
        lines.append(
            f"Relevance labels come from `{reviewer or 'unknown'}` and are an auditable baseline, not candidate ground truth."
        )
    lines.append("")

    if label_cases is not None:
        lines.append("## Audited Metrics")
        lines.append("")
        lines.append(
            "| JD | Matcher | Selected | Useful precision | Useful+marginal precision | Useful recall | Top-3 supported | Irrelevant selected | Missed useful |"
        )
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for item in evals:
            ratings = label_cases.get(item.jd_path.name, {})
            for matcher_name, result in (("keyword", item.keyword), ("semantic", item.semantic)):
                matches = result.strong_matches + result.weak_matches
                metrics = score_matches(matches, ratings)
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            item.jd_path.name,
                            matcher_name,
                            str(metrics.selected_count),
                            _pct(metrics.useful_precision),
                            _pct(metrics.supported_precision),
                            _pct(metrics.useful_recall),
                            _pct(metrics.top3_supported_precision),
                            ", ".join(metrics.irrelevant_ids) or "None",
                            ", ".join(metrics.missed_useful_ids) or "None",
                        ]
                    )
                    + " |"
                )
        lines.append("")

    lines.append("## Summary Table")
    lines.append("")
    lines.append("| JD | Job Type | Keyword Strong | Keyword Weak | Semantic Strong | Semantic Weak | Not Writable | Delta |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in evals:
        ratings = label_cases.get(item.jd_path.name, {}) if label_cases is not None else {}
        format_matches = (
            (lambda matches: _format_audited_matches(matches, ratings))
            if label_cases is not None
            else _format_matches
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    item.jd_path.name,
                    item.keyword.job_type,
                    format_matches(item.keyword.strong_matches),
                    format_matches(item.keyword.weak_matches),
                    format_matches(item.semantic.strong_matches),
                    format_matches(item.semantic.weak_matches),
                    _format_terms(item.keyword),
                    _format_delta(item.keyword, item.semantic),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## Review Questions")
    lines.append("")
    lines.append("- Did semantic add a fact that is truly supported by the fact bank?")
    lines.append("- Did semantic remove a useful keyword match?")
    lines.append("- Did any not-writable item leak into a matched fact instead of staying blocked?")
    lines.append("- Should semantic remain opt-in, become an auxiliary review section, or replace keyword for this JD type?")
    lines.append("")
    return "\n".join(lines)


def default_jd_paths(project_root: Path) -> list[Path]:
    return sorted((project_root / "data" / "sample_jds").glob("*.md"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare keyword and semantic matchers across JD files.")
    parser.add_argument("paths", nargs="*", type=Path, help="JD markdown files. Defaults to data/sample_jds/*.md")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown report path. Defaults to data/evaluation/matcher_report.md",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="Audited relevance labels. Defaults to data/evaluation/matcher_labels.json when present.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root: Path = args.project_root
    jd_paths = args.paths or default_jd_paths(project_root)
    if not jd_paths:
        print("No JD files found.")
        return 1

    try:
        evals = [evaluate_jd(path) for path in jd_paths]
    except ModuleNotFoundError as exc:
        print(f"Matcher evaluation failed: missing dependency for semantic matcher: {exc.name}")
        return 2

    labels_path = args.labels or project_root / "data" / "evaluation" / "matcher_labels.json"
    reviewer = None
    label_cases = None
    if labels_path.exists():
        reviewer, label_cases = load_audit_labels(labels_path)

    output_path = args.output or project_root / "data" / "evaluation" / "matcher_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_markdown(evals, label_cases=label_cases, reviewer=reviewer),
        encoding="utf-8",
    )
    print(f"Matcher evaluation written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

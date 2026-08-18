from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Literal

from .fact_store import load_facts
from .rules import JOB_TYPE_RULES, Fact
from .rules import find_not_writable as _rules_find_not_writable
from .semantic.thresholds import ANALYSIS_MIN_SEMANTIC_SCORE, ANALYSIS_STRONG_SEMANTIC_SCORE

MatcherName = Literal["keyword", "semantic"]
LIST_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.、])\s*(.+)")
LOW_SIGNAL_LINE_RE = re.compile(r"^(?:基础条件|能力特质|开源贡献|技术影响力)[:：]")


@dataclass
class FactMatch:
    fact: Fact
    matched_keywords: list[str]
    level: str


@dataclass
class AnalysisResult:
    job_type: str
    strong_matches: list[FactMatch]
    weak_matches: list[FactMatch]
    not_writable: dict[str, str]
    recommendations: list[str]
    risks: list[str]


def normalize_text(text: str) -> str:
    return text.lower()


def contains_keyword(text: str, keyword: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", keyword):
        return keyword in text
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(keyword.lower())}(?![A-Za-z0-9_])")
    return bool(pattern.search(text.lower()))


def infer_job_type(jd_text: str) -> str:
    scores: list[tuple[str, int]] = []
    for job_type, keywords in JOB_TYPE_RULES:
        score = sum(1 for keyword in keywords if contains_keyword(jd_text, keyword))
        scores.append((job_type, score))
    scores.sort(key=lambda item: item[1], reverse=True)
    if not scores or scores[0][1] == 0:
        return "Unknown / needs manual review"
    return scores[0][0]


def find_not_writable(jd_text: str, facts: Iterable[Fact] | None = None) -> dict[str, str]:
    """Thin wrapper: `rules.find_not_writable` checks JD mentions against the
    fact bank so a term stops being flagged once real evidence exists for
    it (see rules.has_fact_evidence). Loads facts if the caller didn't
    already have a snapshot in hand."""
    return _rules_find_not_writable(jd_text, facts if facts is not None else load_facts())


def match_facts(jd_text: str, facts: Iterable[Fact] | None = None) -> tuple[list[FactMatch], list[FactMatch]]:
    if facts is None:
        facts = load_facts()
    strong: list[FactMatch] = []
    weak: list[FactMatch] = []
    for fact in facts:
        matched = [keyword for keyword in fact.keywords if contains_keyword(jd_text, keyword)]
        if len(matched) >= 2:
            strong.append(FactMatch(fact=fact, matched_keywords=matched, level="strong"))
        elif len(matched) == 1:
            weak.append(FactMatch(fact=fact, matched_keywords=matched, level="weak"))
    strong.sort(key=lambda item: len(item.matched_keywords), reverse=True)
    weak.sort(key=lambda item: len(item.matched_keywords), reverse=True)
    return strong, weak


def merge_keyword_floor(
    semantic_matches: Iterable[FactMatch],
    keyword_matches: Iterable[FactMatch],
) -> list[FactMatch]:
    """Keep exact keyword evidence even when vector top-k misses it."""
    merged: dict[str, FactMatch] = {match.fact.id: match for match in semantic_matches}
    for keyword_match in keyword_matches:
        existing = merged.get(keyword_match.fact.id)
        if existing is None:
            merged[keyword_match.fact.id] = keyword_match
            continue

        labels = list(existing.matched_keywords)
        labels.extend(label for label in keyword_match.matched_keywords if label not in labels)
        level = "strong" if "strong" in {existing.level, keyword_match.level} else "weak"
        merged[keyword_match.fact.id] = FactMatch(
            fact=existing.fact,
            matched_keywords=labels,
            level=level,
        )
    return list(merged.values())


def extract_requirement_lines(jd_text: str) -> list[str]:
    lines: list[str] = []
    for raw in jd_text.splitlines():
        match = LIST_LINE_RE.match(raw)
        if match:
            text = match.group(1).strip()
            if len(text) >= 6:
                lines.append(text)
    return lines


def is_low_signal_requirement_line(line: str) -> bool:
    return bool(LOW_SIGNAL_LINE_RE.search(line))


def match_facts_semantic(jd_text: str, facts: Iterable[Fact] | None = None) -> tuple[list[FactMatch], list[FactMatch]]:
    from .semantic.guarded_search import guarded_semantic_search
    from .semantic.index import load_or_build_index

    fact_items = tuple(facts or load_facts())
    fact_by_id = {fact.id: fact for fact in fact_items}
    lines = [line for line in extract_requirement_lines(jd_text) if not is_low_signal_requirement_line(line)]
    if not lines:
        lines = [jd_text]
    index = load_or_build_index()
    aggregate: dict[str, dict[str, object]] = {}

    for line in lines:
        guarded = guarded_semantic_search(line, index=index, top_k=2, min_score=ANALYSIS_MIN_SEMANTIC_SCORE, facts=fact_items)
        for match in guarded.matches:
            fact_id = match.chunk.fact_id
            if fact_id not in fact_by_id:
                continue
            row = aggregate.setdefault(fact_id, {"score": 0.0, "count": 0})
            row["score"] = max(float(row["score"]), match.score)
            row["count"] = int(row["count"]) + 1

    ranked: list[tuple[FactMatch, float, int]] = []
    for fact_id, row in aggregate.items():
        score = float(row["score"])
        count = int(row["count"])
        fact = fact_by_id[fact_id]
        labels = [f"semantic_score={score:.3f}", f"matched_lines={count}"]
        ranked.append(
            (
                FactMatch(
                    fact=fact,
                    matched_keywords=labels,
                    level="strong" if score >= ANALYSIS_STRONG_SEMANTIC_SCORE or count >= 2 else "weak",
                ),
                score,
                count,
            )
        )

    ranked.sort(key=lambda item: (item[0].level == "strong", item[1], item[2]), reverse=True)
    semantic_matches = [match for match, _, _ in ranked]
    keyword_strong, keyword_weak = match_facts(jd_text, fact_items)
    merged_matches = merge_keyword_floor(semantic_matches, [*keyword_strong, *keyword_weak])
    merged_matches.sort(key=lambda item: (item.level == "strong", len(item.matched_keywords)), reverse=True)
    strong = [match for match in merged_matches if match.level == "strong"]
    weak = [match for match in merged_matches if match.level == "weak"]
    return strong, weak


def build_recommendations(result: AnalysisResult) -> list[str]:
    recommendations: list[str] = []
    matched_titles = [match.fact.title for match in [*result.strong_matches, *result.weak_matches]]
    review_order = ", ".join(dict.fromkeys(matched_titles))
    if result.job_type == "Data application / data engineering":
        recommendations.append(
            f"Review the matched evidence in matcher order: {review_order}."
            if review_order
            else "No matched evidence is available for ordering; manual review is required."
        )
    elif result.job_type == "AI application / Agent engineering":
        recommendations.append(
            f"Review the matched evidence in matcher order: {review_order}."
            if review_order
            else "No matched evidence is available for ordering; manual review is required."
        )
    elif result.job_type == "Algorithm / multimodal research":
        recommendations.append(
            f"Review the matched evidence in matcher order: {review_order}."
            if review_order
            else "No matched evidence is available for ordering; manual review is required."
        )
    else:
        recommendations.append("Manual review needed: job type is not strongly identified.")

    recommendations.append(
        "Matcher order is triage guidance, not final resume selection. "
        "Only candidate-confirmed A/B items may be included, and the generator must not silently omit them."
    )

    if result.not_writable:
        recommendations.append("Do not include not-writable technologies in resume bullets unless a real project is added first.")
    return recommendations


def build_risks(strong: list[FactMatch], weak: list[FactMatch], not_writable: dict[str, str]) -> list[str]:
    risks: list[str] = []
    for match in strong[:5]:
        if match.fact.risk in {"medium", "high"}:
            risks.append(f"{match.fact.title}: prepare boundaries - {'; '.join(match.fact.boundaries)}")
    for tech, reason in not_writable.items():
        risks.append(f"{tech}: not writable. {reason}")
    for match in weak[:3]:
        risks.append(
            f"Lower retrieval signal: {match.fact.title} only matched {', '.join(match.matched_keywords)}; "
            "this is not an editorial ranking."
        )
    return risks


def analyze_jd(jd_text: str, matcher: MatcherName = "keyword") -> AnalysisResult:
    if matcher not in {"keyword", "semantic"}:
        raise ValueError("matcher must be 'keyword' or 'semantic'")
    facts = load_facts()
    job_type = infer_job_type(jd_text)
    not_writable = find_not_writable(jd_text, facts)
    if matcher == "semantic":
        strong, weak = match_facts_semantic(jd_text, facts)
    else:
        strong, weak = match_facts(jd_text, facts)
    provisional = AnalysisResult(
        job_type=job_type,
        strong_matches=strong,
        weak_matches=weak,
        not_writable=not_writable,
        recommendations=[],
        risks=[],
    )
    provisional.recommendations = build_recommendations(provisional)
    provisional.risks = build_risks(strong, weak, not_writable)
    return provisional


def slugify(text: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", text.strip())
    normalized = normalized.strip("_")
    return normalized[:80] or "unknown_jd"


def save_jd_memory(jd_text: str, library_dir: Path, name_hint: str | None = None) -> Path:
    library_dir.mkdir(parents=True, exist_ok=True)
    first_heading = next((line.strip("# ").strip() for line in jd_text.splitlines() if line.strip()), "jd")
    slug = slugify(name_hint or first_heading)
    path = library_dir / f"{slug}.md"
    if path.exists() and path.read_text(encoding="utf-8") == jd_text:
        return path

    counter = 2
    while path.exists():
        if path.read_text(encoding="utf-8") == jd_text:
            return path
        path = library_dir / f"{slug}_{counter}.md"
        counter += 1
    path.write_text(jd_text, encoding="utf-8")
    return path

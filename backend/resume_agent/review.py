from __future__ import annotations

from pathlib import Path

from .analyzer import AnalysisResult, FactMatch
from .fragments import ResumeFragment, load_fragments


MASTERY_OPTIONS = (
    "A 本轮采用核心版 / "
    "B 本轮采用保守版 / "
    "C 本轮不使用这段经历 / "
    "D 事实记录有误，需要回查事实库"
)


def _render_match_block(match: FactMatch, default_decision: str) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {match.fact.title}")
    lines.append("")
    lines.append(f"- fact_id: `{match.fact.id}`")
    lines.append(f"- match_level: `{match.level}`")
    lines.append(f"- matched_keywords: {', '.join(match.matched_keywords)}")
    lines.append(f"- risk_level: `{match.fact.risk}`")
    lines.append(f"- evidence: {match.fact.summary}")
    lines.append(f"- boundaries: {'; '.join(match.fact.boundaries)}")
    lines.append("")
    lines.append(f"- mastery_check: `{default_decision}`")
    lines.append(f"- allowed_options: {MASTERY_OPTIONS}")
    lines.append("- what_i_can_explain: ")
    lines.append("- what_i_cannot_explain_yet: ")
    lines.append("- allowed_resume_intensity: ")
    lines.append("")
    return lines


def _render_composite_match_block(
    fragment: ResumeFragment,
    matches: list[FactMatch],
    default_decision: str,
) -> list[str]:
    risks = [match.fact.risk for match in matches]
    risk = "high" if "high" in risks else "medium" if "medium" in risks else "low"
    levels = {match.level for match in matches}
    level = "strong" if "strong" in levels else "weak"
    matched_keywords: list[str] = []
    evidence: list[str] = []
    boundaries: list[str] = []
    for match in matches:
        matched_keywords.extend(keyword for keyword in match.matched_keywords if keyword not in matched_keywords)
        evidence.append(f"{match.fact.id}: {match.fact.summary}")
        boundaries.extend(boundary for boundary in match.fact.boundaries if boundary not in boundaries)

    lines: list[str] = []
    lines.append(f"### {fragment.title}")
    lines.append("")
    lines.append(f"- display_fact_id: `{fragment.fact_id}`")
    for fact_id in fragment.source_fact_ids:
        lines.append(f"- fact_id: `{fact_id}`")
    lines.append(f"- match_level: `{level}`")
    lines.append(f"- matched_keywords: {', '.join(matched_keywords)}")
    lines.append(f"- risk_level: `{risk}`")
    lines.append(f"- evidence: {' | '.join(evidence)}")
    lines.append(f"- boundaries: {'; '.join(boundaries)}")
    lines.append("")
    lines.append(f"- mastery_check: `{default_decision}`")
    lines.append(f"- allowed_options: {MASTERY_OPTIONS}")
    lines.append("- what_i_can_explain: ")
    lines.append("- what_i_cannot_explain_yet: ")
    lines.append("- allowed_resume_intensity: ")
    lines.append("")
    return lines


def _coalesce_display_matches(
    strong_matches: list[FactMatch],
    weak_matches: list[FactMatch],
    project_root: Path | None,
) -> tuple[list[FactMatch | list[FactMatch]], list[FactMatch | list[FactMatch]]]:
    if project_root is None:
        return strong_matches, weak_matches
    fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json")
    composites = [fragment for fragment in fragments.values() if len(fragment.source_fact_ids) > 1]
    all_matches = [*strong_matches, *weak_matches]
    remaining = {match.fact.id: match for match in all_matches}
    strong_items: list[FactMatch | list[FactMatch]] = []
    weak_items: list[FactMatch | list[FactMatch]] = []

    for fragment in composites:
        source_matches = [remaining.get(fact_id) for fact_id in fragment.source_fact_ids]
        if all(source_matches):
            item = [match for match in source_matches if match is not None]
            if any(match.level == "strong" for match in item):
                strong_items.append(item)
            else:
                weak_items.append(item)
            for fact_id in fragment.source_fact_ids:
                remaining.pop(fact_id, None)

    strong_items.extend(match for match in strong_matches if match.fact.id in remaining)
    weak_items.extend(match for match in weak_matches if match.fact.id in remaining)
    return strong_items, weak_items


def _render_review_item(
    item: FactMatch | list[FactMatch],
    default_decision: str,
    project_root: Path | None,
) -> list[str]:
    if isinstance(item, list):
        fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json") if project_root else {}
        source_ids = tuple(match.fact.id for match in item)
        fragment = next(
            (
                fragment
                for fragment in fragments.values()
                if tuple(fragment.source_fact_ids) == source_ids
            ),
            None,
        )
        if fragment is not None:
            return _render_composite_match_block(fragment, item, default_decision)
    return _render_match_block(item if isinstance(item, FactMatch) else item[0], default_decision)


def semantic_only_candidates(primary: AnalysisResult, semantic: AnalysisResult) -> list[FactMatch]:
    primary_ids = {match.fact.id for match in primary.strong_matches + primary.weak_matches}
    return [
        match
        for match in semantic.strong_matches + semantic.weak_matches
        if match.fact.id not in primary_ids
    ]


def _render_semantic_candidate_block(match: FactMatch) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {match.fact.title}")
    lines.append("")
    lines.append(f"- candidate_id: `{match.fact.id}`")
    lines.append(f"- semantic_signal: {', '.join(match.matched_keywords)}")
    lines.append(f"- risk_level: `{match.fact.risk}`")
    lines.append(f"- evidence: {match.fact.summary}")
    lines.append(f"- boundaries: {'; '.join(match.fact.boundaries)}")
    lines.append("- triage_note: semantic-only candidate; not used by decide/generate unless promoted into the main review sections.")
    lines.append("")
    return lines


def render_review_sheet(
    result: AnalysisResult,
    jd_path: Path,
    semantic_candidates: list[FactMatch] | None = None,
    semantic_note: str | None = None,
    project_root: Path | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# JD Resume Review Sheet")
    lines.append("")
    lines.append(f"- JD source: `{jd_path}`")
    lines.append(f"- Inferred job type: **{result.job_type}**")
    lines.append("- Overall application decision: `待确认`")
    lines.append("- Overall note: ")
    lines.append("")
    lines.append("## How To Review")
    lines.append("")
    lines.append("For each fact below, answer only one practical question:")
    lines.append("")
    lines.append("How strongly may this fact be used in this specific application?")
    lines.append("")
    lines.append(f"`{MASTERY_OPTIONS}`")
    lines.append("")
    lines.append("The fact bank records candidate-confirmed experience. This review selects the concrete wording used for this JD.")
    lines.append("Use A for the core fragment, B for the conservative fragment, C to omit it from this application, and D when the fact record itself needs correction.")
    lines.append("The resume generator only uses A/B items. C/D items are blocked by default.")
    lines.append("Semantic Candidates, when present, are review hints only and are not used by the generator.")
    lines.append("")

    strong_items, weak_items = _coalesce_display_matches(result.strong_matches, result.weak_matches, project_root)

    lines.append("## Strong Matches")
    if strong_items:
        for item in strong_items:
            lines.extend(_render_review_item(item, "待确认", project_root))
    else:
        lines.append("- None")
        lines.append("")

    lines.append("## Weak Matches")
    if weak_items:
        for item in weak_items:
            lines.extend(_render_review_item(item, "降权", project_root))
    else:
        lines.append("- None")
        lines.append("")

    if semantic_candidates is not None or semantic_note:
        lines.append("## Semantic Candidates")
        lines.append("")
        lines.append("These are semantic-only hints for manual triage. They are not A/B/C/D items and cannot enter the resume unless manually promoted into Strong/Weak Matches.")
        lines.append("")
        if semantic_note:
            lines.append(f"- Note: {semantic_note}")
            lines.append("")
        if semantic_candidates:
            for match in semantic_candidates:
                lines.extend(_render_semantic_candidate_block(match))
        elif not semantic_note:
            lines.append("- None")
            lines.append("")

    lines.append("## Not Writable Items")
    if result.not_writable:
        for tech, reason in result.not_writable.items():
            lines.append(f"### {tech}")
            lines.append("")
            lines.append("- mastery_check: `D 没有对应能力，需要补项目`")
            lines.append(f"- reason: {reason}")
            lines.append("- possible_action: add a real project record before using this keyword.")
            lines.append("")
    else:
        lines.append("- None detected")
        lines.append("")

    lines.append("## Resume Strategy Draft")
    for item in result.recommendations:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Interview Preparation")
    if result.risks:
        for risk in result.risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- No major risks detected by current rules.")
    lines.append("")
    return "\n".join(lines)


def write_review_sheet(review: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / "review_sheet.md"
    review_path.write_text(review, encoding="utf-8")
    return review_path

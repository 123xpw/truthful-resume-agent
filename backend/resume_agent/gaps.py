from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .fact_store import load_facts
from .fragments import ResumeFragment, load_fragments
from .quality import ResumeQuality, check_resume_quality, render_quality
from .review import MASTERY_OPTIONS
from .review_parser import parse_review_mastery


FACT_ID_LINE_RE = re.compile(r"^- fact_id:\s*`([^`]+)`", re.MULTILINE)


@dataclass(frozen=True)
class GapCandidate:
    fact_id: str
    section: str
    title: str
    reason: str
    current_decision: str


@dataclass(frozen=True)
class GapReport:
    name: str
    quality: ResumeQuality
    candidates: tuple[GapCandidate, ...]


@dataclass(frozen=True)
class ExpandReviewResult:
    review_path: Path
    added: int
    skipped_existing: int
    added_fact_ids: tuple[str, ...]


def _candidate_reason(fragment: ResumeFragment, quality: ResumeQuality) -> str:
    if fragment.section == "实习经历" and quality.internship_entries < 2:
        return "can help fill missing internship coverage"
    if fragment.section == "项目经历" and quality.project_entries < 2:
        return "can help fill missing project coverage"
    return "can add professional content density"


def build_gap_report(project_root: Path, name: str) -> GapReport:
    output_dir = project_root / "data" / "outputs" / name
    review_path = output_dir / "review_sheet.md"
    tex_path = output_dir / "resume_draft.tex"
    pdf_path = output_dir / "resume_draft.pdf"
    quality = check_resume_quality(review_path=review_path, tex_path=tex_path, pdf_path=pdf_path)
    mastery = parse_review_mastery(review_path) if review_path.exists() else {}
    fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json")
    selected = {fact_id for fact_id, decision in mastery.items() if decision in {"A", "B"}}

    candidates: list[GapCandidate] = []
    for fact_id, fragment in fragments.items():
        if all(source_fact_id in selected for source_fact_id in fragment.source_fact_ids):
            continue
        if fragment.section not in {"实习经历", "项目经历"}:
            continue
        candidates.append(
            GapCandidate(
                fact_id=fact_id,
                section=fragment.section,
                title=fragment.title,
                reason=_candidate_reason(fragment, quality),
                current_decision=mastery.get(fact_id, "not_in_review"),
            )
        )

    return GapReport(name=name, quality=quality, candidates=tuple(candidates))


def _existing_fact_ids(review_text: str) -> set[str]:
    return set(FACT_ID_LINE_RE.findall(review_text))


def _render_gap_review_block(candidate: GapCandidate, project_root: Path) -> list[str]:
    facts = {fact.id: fact for fact in load_facts(project_root / "data" / "facts" / "facts.json")}
    fact = facts.get(candidate.fact_id)
    if fact is None:
        return []

    lines: list[str] = []
    lines.append(f"### {fact.title}")
    lines.append("")
    lines.append(f"- fact_id: `{fact.id}`")
    lines.append("- match_level: `gap_candidate`")
    lines.append(f"- suggested_section: `{candidate.section}`")
    lines.append(f"- gap_reason: {candidate.reason}")
    lines.append(f"- risk_level: `{fact.risk}`")
    lines.append(f"- evidence: {fact.summary}")
    lines.append(f"- boundaries: {'; '.join(fact.boundaries)}")
    lines.append("")
    lines.append("- mastery_check: `待确认`")
    lines.append(f"- allowed_options: {MASTERY_OPTIONS}")
    lines.append("- what_i_can_explain:")
    lines.append("- what_i_cannot_explain_yet:")
    lines.append("- allowed_resume_intensity:")
    lines.append("")
    return lines


def expand_review_from_gaps(project_root: Path, name: str) -> ExpandReviewResult:
    output_dir = project_root / "data" / "outputs" / name
    review_path = output_dir / "review_sheet.md"
    if not review_path.exists():
        raise FileNotFoundError(f"Review sheet not found: {review_path}")

    report = build_gap_report(project_root, name)
    review_text = review_path.read_text(encoding="utf-8")
    existing = _existing_fact_ids(review_text)
    blocks: list[str] = []
    added_fact_ids: list[str] = []
    skipped_existing = 0

    for candidate in report.candidates:
        if candidate.fact_id in existing:
            skipped_existing += 1
            continue
        block = _render_gap_review_block(candidate, project_root)
        if not block:
            continue
        blocks.extend(block)
        added_fact_ids.append(candidate.fact_id)

    if blocks:
        lines = [review_text.rstrip(), "", "## Gap Review Candidates", ""]
        lines.append("These candidates were added from the gap report. They remain blocked until A/B/C/D confirmation.")
        lines.append("")
        lines.extend(blocks)
        review_path.write_text("\n".join(lines), encoding="utf-8")

    return ExpandReviewResult(
        review_path=review_path,
        added=len(added_fact_ids),
        skipped_existing=skipped_existing,
        added_fact_ids=tuple(added_fact_ids),
    )


def render_gap_report(report: GapReport) -> str:
    lines = [
        f"# Gap Report: {report.name}",
        "",
        render_quality(report.quality),
        "",
        "## Candidate Facts To Review",
        "",
        "These are not automatically writable. Promote them only through the review sheet after A/B/C/D confirmation.",
        "",
    ]
    if not report.candidates:
        lines.append("- None")
    else:
        for candidate in report.candidates:
            lines.append(f"- `{candidate.fact_id}` [{candidate.section}] {candidate.title}")
            lines.append(f"  - current_decision: {candidate.current_decision}")
            lines.append(f"  - reason: {candidate.reason}")
    lines.append("")
    return "\n".join(lines)


def write_gap_report(report: GapReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "gap_report.md"
    path.write_text(render_gap_report(report), encoding="utf-8")
    return path

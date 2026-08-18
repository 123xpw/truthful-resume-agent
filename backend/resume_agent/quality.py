from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .review_parser import parse_review_decisions


SECTION_RE = re.compile(r"\\section\{([^}]+)\}")
ENTRY_RE = re.compile(r"\\entry\{")
PROJECT_ENTRY_RE = re.compile(r"\\projectEntry(?:Url)?\{")
ITEM_RE = re.compile(r"^\s*\\item\b", re.MULTILINE)


@dataclass(frozen=True)
class ResumeQuality:
    status: str
    professional_entries: int
    internship_entries: int
    project_entries: int
    professional_bullets: int
    a_count: int
    b_count: int
    unverified_ab_count: int
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == "structure_ready"


def _section_body(tex: str, section_name: str) -> str:
    matches = list(SECTION_RE.finditer(tex))
    for index, match in enumerate(matches):
        if match.group(1) != section_name:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(tex)
        return tex[start:end]
    return ""


def _professional_bullets(tex: str) -> int:
    sections = [
        _section_body(tex, "实习经历"),
        _section_body(tex, "项目经历"),
    ]
    return sum(len(ITEM_RE.findall(section)) for section in sections)


def check_resume_quality(review_path: Path, tex_path: Path, pdf_path: Path | None = None) -> ResumeQuality:
    reasons: list[str] = []
    if not tex_path.exists():
        reasons.append(f"resume tex missing: {tex_path}")
        return ResumeQuality("blocked", 0, 0, 0, 0, 0, 0, 0, tuple(reasons))

    tex = tex_path.read_text(encoding="utf-8")
    decisions = parse_review_decisions(review_path) if review_path.exists() else {}
    confirmed_mastery = {
        fact_id: decision.mastery
        for fact_id, decision in decisions.items()
        if decision.is_interactively_confirmed
    }
    unverified_ab_ids = [
        fact_id
        for fact_id, decision in decisions.items()
        if decision.mastery in {"A", "B"} and not decision.is_interactively_confirmed
    ]
    a_count = sum(1 for value in confirmed_mastery.values() if value == "A")
    b_count = sum(1 for value in confirmed_mastery.values() if value == "B")
    internship_entries = len(ENTRY_RE.findall(_section_body(tex, "实习经历")))
    project_entries = len(PROJECT_ENTRY_RE.findall(_section_body(tex, "项目经历")))
    professional_entries = internship_entries + project_entries
    professional_bullets = _professional_bullets(tex)

    if pdf_path is not None and not pdf_path.exists():
        reasons.append(f"resume pdf missing: {pdf_path}")
    if unverified_ab_ids:
        reasons.append(
            "A/B decisions missing interactive confirmation marker: "
            + ", ".join(sorted(unverified_ab_ids))
        )
    if a_count == 0:
        reasons.append("no interactively confirmed A-level facts")
    if internship_entries < 2:
        reasons.append(f"too few internship entries: {internship_entries} < 2")
    if internship_entries > 3:
        reasons.append(
            f"too many internship entries for the one-page policy: {internship_entries} > 3; "
            "revisit the application decisions and mark omitted items C"
        )
    if project_entries < 2:
        reasons.append(f"too few project entries: {project_entries} < 2")
    if project_entries > 2:
        reasons.append(
            f"too many project entries for the one-page policy: {project_entries} > 2; "
            "run decide --revisit and mark the project omitted from this application as C"
        )
    if professional_entries < 4:
        reasons.append(f"too few professional entries: {professional_entries} < 4")
    if professional_bullets < 8:
        reasons.append(f"too few professional bullets: {professional_bullets} < 8")

    return ResumeQuality(
        status="needs_review" if reasons else "structure_ready",
        professional_entries=professional_entries,
        internship_entries=internship_entries,
        project_entries=project_entries,
        professional_bullets=professional_bullets,
        a_count=a_count,
        b_count=b_count,
        unverified_ab_count=len(unverified_ab_ids),
        reasons=tuple(reasons),
    )


def render_quality(quality: ResumeQuality) -> str:
    lines = [
        f"Structure check: {quality.status}",
        (
            "Structure metrics: "
            f"entries={quality.professional_entries} "
            f"(internships={quality.internship_entries}, projects={quality.project_entries}), "
            f"professional_bullets={quality.professional_bullets}, "
            f"confirmed_A={quality.a_count}, confirmed_B={quality.b_count}, "
            f"unverified_A_or_B={quality.unverified_ab_count}"
        ),
    ]
    if quality.reasons:
        lines.append("Structure / confirmation reasons:")
        lines.extend(f"- {reason}" for reason in quality.reasons)
    return "\n".join(lines)

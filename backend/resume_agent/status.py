from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .quality import ResumeQuality, check_resume_quality, render_quality
from .review_parser import count_pending_review_items, parse_review_decisions, parse_review_mastery


@dataclass(frozen=True)
class ApplicationStatus:
    name: str
    jd_path: Path
    output_dir: Path
    has_jd: bool
    has_report: bool
    has_review: bool
    has_tex: bool
    has_pdf: bool
    tex_fresh: bool
    pdf_fresh: bool
    accepted_count: int
    unverified_accepted_count: int
    blocked_count: int
    pending_count: int
    quality: ResumeQuality | None
    next_step: str


def status_stage(status: ApplicationStatus) -> str:
    if not status.has_jd or not status.has_report or not status.has_review:
        return "prepare"
    if status.pending_count:
        return "decide"
    if status.unverified_accepted_count:
        return "re_confirm"
    if status.accepted_count == 0:
        return "blocked"
    if not status.has_tex or not status.tex_fresh:
        return "finalize"
    if not status.has_pdf or not status.pdf_fresh:
        return "tex_ready"
    if status.quality and status.quality.passed:
        return "export_ready"
    return "draft_ready"


def _artifact_label(status: ApplicationStatus, exists: bool, fresh: bool) -> str:
    if not exists:
        return "no"
    decisions_ready = (
        status.pending_count == 0
        and status.unverified_accepted_count == 0
        and status.accepted_count > 0
    )
    if not decisions_ready:
        return "unconfirmed"
    return "yes" if fresh else "stale"


def _is_fresh(artifact: Path, dependencies: tuple[Path, ...]) -> bool:
    if not artifact.exists():
        return False
    artifact_mtime = artifact.stat().st_mtime_ns
    return all(not dependency.exists() or artifact_mtime >= dependency.stat().st_mtime_ns for dependency in dependencies)


def _count_pending(review_path: Path) -> int:
    return count_pending_review_items(review_path)


def inspect_application(project_root: Path, name: str) -> ApplicationStatus:
    jd_path = project_root / "data" / "jd_library" / f"{name}.md"
    output_dir = project_root / "data" / "outputs" / name
    report_path = output_dir / "match_report.md"
    review_path = output_dir / "review_sheet.md"
    tex_path = output_dir / "resume_draft.tex"
    pdf_path = output_dir / "resume_draft.pdf"
    fragment_private = project_root / "data" / "resume_fragments" / "fragments.json"
    fragment_example = fragment_private.with_name("fragments.example.json")
    profile_private = project_root / "data" / "profile" / "profile.private.json"
    profile_example = profile_private.with_name("profile.example.json")
    generator_path = Path(__file__).with_name("resume_generator.py")

    decisions = parse_review_decisions(review_path) if review_path.exists() else {}
    mastery = parse_review_mastery(review_path) if review_path.exists() else {}
    accepted_count = sum(1 for value in mastery.values() if value in {"A", "B"})
    unverified_accepted_count = sum(
        1
        for decision in decisions.values()
        if decision.mastery in {"A", "B"} and not decision.is_interactively_confirmed
    )
    blocked_count = sum(1 for value in mastery.values() if value in {"C", "D"})
    pending_count = _count_pending(review_path)
    generation_dependencies = (
        jd_path,
        review_path,
        fragment_private if fragment_private.exists() else fragment_example,
        profile_private if profile_private.exists() else profile_example,
        generator_path,
    )
    tex_fresh = _is_fresh(tex_path, generation_dependencies)
    pdf_fresh = tex_fresh and _is_fresh(pdf_path, (tex_path,))
    quality = (
        check_resume_quality(review_path=review_path, tex_path=tex_path, pdf_path=pdf_path)
        if (
            review_path.exists()
            and tex_path.exists()
            and pending_count == 0
            and unverified_accepted_count == 0
            and accepted_count > 0
            and tex_fresh
            and pdf_fresh
        )
        else None
    )

    if not jd_path.exists() or not report_path.exists() or not review_path.exists():
        next_step = f"prepare: python3 backend/run_cli.py prepare --file <jd> --name {name}"
    elif pending_count:
        next_step = f"decide: python3 backend/run_cli.py decide --name {name}"
    elif unverified_accepted_count:
        next_step = f"re-confirm: python3 backend/run_cli.py decide --name {name}"
    elif accepted_count == 0:
        next_step = "blocked: no A/B facts are confirmed, so no resume can be generated"
    elif not tex_path.exists() or not tex_fresh:
        next_step = f"finalize: python3 backend/run_cli.py finalize --name {name}"
    elif not pdf_path.exists() or not pdf_fresh:
        next_step = f"optional pdf: python3 backend/run_cli.py finalize --name {name} --pdf"
    elif quality and not quality.passed:
        next_step = "review: draft exists, but structure or interactive confirmation checks are not satisfied"
    else:
        next_step = "export-ready: structure checks and interactive confirmation markers are present"

    return ApplicationStatus(
        name=name,
        jd_path=jd_path,
        output_dir=output_dir,
        has_jd=jd_path.exists(),
        has_report=report_path.exists(),
        has_review=review_path.exists(),
        has_tex=tex_path.exists(),
        has_pdf=pdf_path.exists(),
        tex_fresh=tex_fresh,
        pdf_fresh=pdf_fresh,
        accepted_count=accepted_count,
        unverified_accepted_count=unverified_accepted_count,
        blocked_count=blocked_count,
        pending_count=pending_count,
        quality=quality,
        next_step=next_step,
    )


def list_applications(project_root: Path) -> list[ApplicationStatus]:
    names: set[str] = set()
    jd_dir = project_root / "data" / "jd_library"
    output_dir = project_root / "data" / "outputs"
    output_artifacts = ("match_report.md", "review_sheet.md", "resume_draft.tex", "resume_draft.pdf")

    if jd_dir.exists():
        names.update(path.stem for path in jd_dir.glob("*.md"))
    if output_dir.exists():
        names.update(
            path.name
            for path in output_dir.iterdir()
            if path.is_dir() and any((path / artifact).exists() for artifact in output_artifacts)
        )

    return [inspect_application(project_root, name) for name in sorted(names)]


def render_status(status: ApplicationStatus) -> str:
    lines = [
        f"Application: {status.name}",
        f"JD saved: {'yes' if status.has_jd else 'no'} ({status.jd_path})",
        f"Match report: {'yes' if status.has_report else 'no'}",
        f"Review sheet: {'yes' if status.has_review else 'no'}",
        f"Resume draft tex: {_artifact_label(status, status.has_tex, status.tex_fresh)}",
        f"Resume draft pdf: {_artifact_label(status, status.has_pdf, status.pdf_fresh)}",
        (
            "Review decisions: "
            f"confirmed A/B={status.accepted_count}, "
            f"unverified A/B={status.unverified_accepted_count}, "
            f"C/D={status.blocked_count}, pending={status.pending_count}"
        ),
        render_quality(status.quality) if status.quality else "Structure check: not checked",
        f"Next step: {status.next_step}",
    ]
    return "\n".join(lines)


def render_application_list(statuses: list[ApplicationStatus]) -> str:
    if not statuses:
        return "No applications found. Run `python3 backend/run_cli.py prepare --file <jd> --name <name>` first."

    headers = ("Name", "Stage", "Structure", "Review", "Tex", "PDF", "Decisions", "Next")
    rows = [
        (
            status.name,
            status_stage(status),
            status.quality.status if status.quality else "not_checked",
            "yes" if status.has_review else "no",
            _artifact_label(status, status.has_tex, status.tex_fresh),
            _artifact_label(status, status.has_pdf, status.pdf_fresh),
            (
                f"confirmed A/B={status.accepted_count}, "
                f"unverified A/B={status.unverified_accepted_count}, "
                f"C/D={status.blocked_count}, pending={status.pending_count}"
            ),
            status.next_step,
        )
        for status in statuses
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return "\n".join(lines)

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from .analyzer import analyze_jd, save_jd_memory, slugify
from .authorization_store import apply_reusable_authorizations, record_authorizations_from_review
from .decision_flow import run_interactive_decision
from .delivery import DeliveryQualityError, deliver_resume
from .gaps import build_gap_report, expand_review_from_gaps, render_gap_report, write_gap_report
from .gap_trends import diff_against_last, load_snapshots, record_gap_snapshot
from .fact_store import load_facts, resolve_facts_path
from .interview_feedback import (
    append_boundary_to_facts,
    load_feedback,
    record_feedback,
    render_feedback,
)
from .jd_insight import build_jd_insight_data, render_gap_warning, render_gap_warning_html, render_markdown, write_jd_insight
from .mastery_history import (
    load_mastery_history,
    render_mastery_history,
)
from .outcomes import VALID_OUTCOMES, default_outcome_path, load_outcomes, record_outcome, render_outcomes
from .profile import load_profile
from .quality import render_quality
from .report import render_markdown_report, write_report
from .resume_generator import generate_resume_tex
from .fragments import load_fragments
from .review import render_review_sheet, semantic_only_candidates, write_review_sheet
from .review_parser import count_pending_review_items, count_unverified_ab_items
from .status import (
    inspect_application,
    list_applications,
    render_application_list,
    render_status,
    review_is_fresh,
)
from .validate import validate_facts_file

MATCHER_HELP = "Fact matcher to use. Default keyword keeps the original conservative behavior."


def read_stdin_until_eof() -> str:
    print("Paste JD text, then press Ctrl-D to finish:", file=sys.stderr)
    return sys.stdin.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Truthful Resume Agent CLI MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a JD from file or pasted stdin")
    input_group = analyze.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    input_group.add_argument("--paste", action="store_true", help="Paste JD through stdin")
    analyze.add_argument("--name", help="Optional stable name for saved JD/output folder")
    analyze.add_argument("--matcher", choices=["keyword", "semantic"], default="keyword", help=MATCHER_HELP)
    analyze.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    review = subparsers.add_parser("review", help="Create a manual review sheet for a saved JD")
    review_input = review.add_mutually_exclusive_group(required=True)
    review_input.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    review_input.add_argument("--name", help="Name of a saved JD in data/jd_library without .md")
    review.add_argument("--matcher", choices=["keyword", "semantic"], default="keyword", help=MATCHER_HELP)
    review.add_argument(
        "--semantic-candidates",
        action="store_true",
        help="Append semantic-only review hints without making them eligible for decide/generate.",
    )
    review.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    validate = subparsers.add_parser("validate", help="Validate structured fact files")
    validate.add_argument(
        "--facts",
        type=Path,
        help="Path to facts.json. Defaults to data/facts/facts.json",
    )
    validate.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    generate = subparsers.add_parser("generate", help="Generate a LaTeX resume draft from a reviewed JD")
    generate_input = generate.add_mutually_exclusive_group(required=True)
    generate_input.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    generate_input.add_argument("--name", help="Name of a saved JD in data/jd_library without .md")
    generate.add_argument("--review", type=Path, help="Path to review_sheet.md. Defaults to data/outputs/<name>/review_sheet.md")
    generate.add_argument("--matcher", choices=["keyword", "semantic"], default="keyword", help=MATCHER_HELP)
    generate.add_argument(
        "--llm-select",
        action="store_true",
        help="Use the configured LLM to rank only confirmed fragment IDs within the 3-internship/3-project limits.",
    )
    generate.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    decide = subparsers.add_parser(
        "decide",
        help="Grant reusable A/B/C/D resume-wording authorization for new or changed items",
    )
    decide_input = decide.add_mutually_exclusive_group(required=True)
    decide_input.add_argument("--review", type=Path, help="Path to review_sheet.md")
    decide_input.add_argument("--name", help="Name of a saved JD output folder")
    decide.add_argument(
        "--notes",
        action="store_true",
        help="Also ask optional authorization/boundary notes after each A/B/C/D choice.",
    )
    decide.add_argument(
        "--revisit",
        action="store_true",
        help="Revisit every existing A/B/C/D decision; Enter keeps the current choice.",
    )
    decide.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    prepare = subparsers.add_parser("prepare", help="Save JD, write match report, and create review sheet")
    prepare_input = prepare.add_mutually_exclusive_group(required=True)
    prepare_input.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    prepare_input.add_argument("--paste", action="store_true", help="Paste JD through stdin")
    prepare.add_argument("--name", required=True, help="Stable name for saved JD/output folder")
    prepare.add_argument(
        "--semantic-candidates",
        action="store_true",
        default=True,
        help="Append semantic-only review hints when dependencies are available. Enabled by default.",
    )
    prepare.add_argument(
        "--no-semantic-candidates",
        dest="semantic_candidates",
        action="store_false",
        help="Skip semantic-only review hints.",
    )
    prepare.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    finalize = subparsers.add_parser("finalize", help="Generate resume draft from a confirmed review sheet")
    finalize_input = finalize.add_mutually_exclusive_group(required=True)
    finalize_input.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    finalize_input.add_argument("--name", help="Name of a saved JD in data/jd_library without .md")
    finalize.add_argument("--review", type=Path, help="Path to review_sheet.md. Defaults to data/outputs/<name>/review_sheet.md")
    finalize.add_argument("--pdf", action="store_true", help="Compile PDF when a supported LaTeX engine is available")
    finalize.add_argument(
        "--llm-select",
        action="store_true",
        help="Use the configured LLM to rank only confirmed fragment IDs within the 3-internship/3-project limits.",
    )
    finalize.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    status = subparsers.add_parser("status", help="Show current workflow state for a saved application")
    status.add_argument("--name", required=True, help="Name of a saved JD/output folder")
    status.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    list_cmd = subparsers.add_parser("list", help="List saved applications and their workflow state")
    list_cmd.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    deliver = subparsers.add_parser("deliver", help="Copy finalized resume files into 投递版本/<company>/")
    deliver.add_argument("--name", required=True, help="Name of a saved JD/output folder")
    deliver.add_argument("--company", required=True, help="Company folder/name under 投递版本")
    deliver.add_argument("--role", required=True, help="Role name used in exported filenames")
    deliver.add_argument("--candidate", help="Candidate name used in exported PDF filename; defaults to profile")
    deliver.add_argument("--school", help="School name used in exported PDF filename; defaults to profile")
    deliver.add_argument("--major", help="Major name used in exported PDF filename; defaults to profile")
    deliver.add_argument("--no-tex", action="store_true", help="Only copy the PDF, not the TeX source")
    deliver.add_argument("--delivery-root", type=Path, help="Destination root. Defaults to ../../投递版本 from project root")
    deliver.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    explain_jd = subparsers.add_parser("explain-jd", help="JD-understanding + fact-gap report (not a resume generator)")
    explain_jd_input = explain_jd.add_mutually_exclusive_group(required=True)
    explain_jd_input.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    explain_jd_input.add_argument("--name", help="Name of a saved JD in data/jd_library without .md")
    explain_jd.add_argument("--matcher", choices=["keyword", "semantic"], default="keyword", help=MATCHER_HELP)
    explain_jd.add_argument("--write", action="store_true", help="Write data/outputs/<name>/jd_insight.md")
    explain_jd.add_argument("--no-llm", action="store_true", help="Skip DeepSeek calls; only render the deterministic sections")
    explain_jd.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    record_outcome_cmd = subparsers.add_parser("record-outcome", help="Record an application status and resume hash")
    record_outcome_cmd.add_argument("--name", required=True, help="Saved application name")
    record_outcome_cmd.add_argument("--status", required=True, choices=sorted(VALID_OUTCOMES))
    record_outcome_cmd.add_argument("--date", help="Event date in YYYY-MM-DD; defaults to today")
    record_outcome_cmd.add_argument("--note", default="", help="Short factual note; do not infer causality")
    record_outcome_cmd.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    list_outcomes_cmd = subparsers.add_parser("list-outcomes", help="List recorded application outcomes")
    list_outcomes_cmd.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    gaps = subparsers.add_parser("gaps", help="Explain why a draft is not deliverable and list review candidates")
    gaps.add_argument("--name", required=True, help="Name of a saved JD/output folder")
    gaps.add_argument("--write", action="store_true", help="Write data/outputs/<name>/gap_report.md")
    gaps.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    gap_check = subparsers.add_parser("gap-check", help="Concise 缺什么预警 for a JD (what to fill / what will be grilled)")
    gap_check_input = gap_check.add_mutually_exclusive_group(required=True)
    gap_check_input.add_argument("--file", type=Path, help="Path to a JD markdown/text file")
    gap_check_input.add_argument("--name", help="Name of a saved JD in data/jd_library without .md")
    gap_check.add_argument("--matcher", choices=["keyword", "semantic"], default="keyword", help=MATCHER_HELP)
    gap_check.add_argument("--write", action="store_true", help="Write data/outputs/<name>/gap_warning.md")
    gap_check.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    career_trends = subparsers.add_parser("career-trends", help="Aggregate 缺什么 across all saved JDs (frequency of unsupported tech)")
    career_trends.add_argument("--write", action="store_true", help="Write data/evaluation/career_trends.md")
    career_trends.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    expand_review = subparsers.add_parser("expand-review", help="Append gap candidates to review_sheet.md as pending questions")
    expand_review.add_argument("--name", required=True, help="Name of a saved JD/output folder")
    expand_review.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    record_interview = subparsers.add_parser(
        "record-interview",
        help="Record an interview question that exposed a fact boundary",
    )
    record_interview.add_argument("--name", required=True, help="Saved application name")
    record_interview.add_argument("--fact-id", required=True, help="Fact ID the question targeted")
    record_interview.add_argument("--question", required=True, help="The interview question asked")
    record_interview.add_argument("--note", default="", help="Short factual note on what was exposed")
    record_interview.add_argument("--date", help="Event date in YYYY-MM-DD; defaults to today")
    record_interview.add_argument(
        "--append-boundary",
        action="store_true",
        help="Also append the note to the fact's boundaries in facts.json (deduped).",
    )
    record_interview.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    list_interview = subparsers.add_parser(
        "list-interview",
        help="List recorded interview feedback for an application",
    )
    list_interview.add_argument("--name", required=True, help="Saved application name")
    list_interview.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )

    mastery_history_cmd = subparsers.add_parser(
        "mastery-history",
        help="Show mastery progression (C->B->A) across decide snapshots",
    )
    mastery_history_cmd.add_argument("--fact-id", help="Show timeline for a single fact ID")
    mastery_history_cmd.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Project root path",
    )
    return parser


def _read_jd_input(args: argparse.Namespace, project_root: Path) -> tuple[Path | None, str, str]:
    if getattr(args, "file", None):
        jd_path = args.file
        return jd_path, jd_path.read_text(encoding="utf-8"), args.name or jd_path.stem
    if getattr(args, "paste", False):
        return None, read_stdin_until_eof(), args.name

    jd_path = project_root / "data" / "jd_library" / f"{args.name}.md"
    if not jd_path.exists():
        raise FileNotFoundError(f"Saved JD not found: {jd_path}")
    return jd_path, jd_path.read_text(encoding="utf-8"), args.name


def _semantic_candidates_for_review(jd_text: str, result) -> tuple[list | None, str | None]:
    try:
        semantic_result = analyze_jd(jd_text, matcher="semantic")
        return semantic_only_candidates(result, semantic_result), None
    except ModuleNotFoundError as exc:
        return [], f"semantic candidates skipped because dependency is missing: {exc.name}"


def _run_pdf_command(command: list[str], tex_path: Path) -> None:
    completed = subprocess.run(command, cwd=tex_path.parent, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"PDF compile failed: {' '.join(command)}")


def _compile_pdf(tex_path: Path) -> Path | None:
    if shutil.which("latexmk"):
        _run_pdf_command(["latexmk", "-xelatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name], tex_path)
    elif shutil.which("xelatex"):
        command = ["xelatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
        _run_pdf_command(command, tex_path)
        _run_pdf_command(command, tex_path)
    elif shutil.which("tectonic"):
        print(
            "PDF skipped: `tectonic` is installed, but this environment cannot launch it reliably from Python.",
            file=sys.stderr,
        )
        print(f"Run manually: cd {tex_path.parent} && tectonic {tex_path.name}", file=sys.stderr)
        return None
    else:
        print("PDF skipped: no supported LaTeX engine found (`latexmk`, `xelatex`, or `tectonic`).", file=sys.stderr)
        return None

    pdf_path = tex_path.with_suffix(".pdf")
    print(f"PDF written: {pdf_path}")
    return pdf_path


def run_analyze(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    if args.file:
        jd_text = args.file.read_text(encoding="utf-8")
        name_hint = args.name or args.file.stem
    else:
        jd_text = read_stdin_until_eof()
        name_hint = args.name

    if not jd_text.strip():
        print("JD text is empty.", file=sys.stderr)
        return 2

    jd_path = save_jd_memory(jd_text, project_root / "data" / "jd_library", name_hint=name_hint)
    try:
        result = analyze_jd(jd_text, matcher=args.matcher)
    except ModuleNotFoundError as exc:
        print(f"Analyze failed: missing dependency for {args.matcher} matcher: {exc.name}", file=sys.stderr)
        return 2
    output_slug = slugify(name_hint or jd_path.stem)
    output_dir = project_root / "data" / "outputs" / output_slug
    report = render_markdown_report(result, jd_path)
    report_path = write_report(report, output_dir)

    print(f"JD saved: {jd_path}")
    print(f"Report written: {report_path}")
    print(f"Job type: {result.job_type}")
    if result.not_writable:
        print("Not writable:", ", ".join(result.not_writable))
    return 0


def run_review(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    if args.file:
        jd_path = args.file
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.file.stem)
    else:
        jd_path = project_root / "data" / "jd_library" / f"{args.name}.md"
        if not jd_path.exists():
            print(f"Saved JD not found: {jd_path}", file=sys.stderr)
            return 2
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.name)

    try:
        result = analyze_jd(jd_text, matcher=args.matcher)
    except ModuleNotFoundError as exc:
        print(f"Review failed: missing dependency for {args.matcher} matcher: {exc.name}", file=sys.stderr)
        return 2
    candidates = None
    candidate_note = None
    if args.semantic_candidates:
        try:
            semantic_result = analyze_jd(jd_text, matcher="semantic")
            candidates = semantic_only_candidates(result, semantic_result)
        except ModuleNotFoundError as exc:
            candidates = []
            candidate_note = f"semantic candidates skipped because dependency is missing: {exc.name}"
    output_dir = project_root / "data" / "outputs" / output_slug
    review = render_review_sheet(
        result,
        jd_path,
        semantic_candidates=candidates,
        semantic_note=candidate_note,
        project_root=project_root,
    )
    review_path = write_review_sheet(review, output_dir)
    reused_count = apply_reusable_authorizations(project_root, review_path)

    print(f"Review sheet written: {review_path}")
    if reused_count:
        print(f"Reused {reused_count} unchanged resume authorization(s).")
    print(f"Job type: {result.job_type}")
    if result.not_writable:
        print("Not writable:", ", ".join(result.not_writable))
    return 0


def run_validate(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    facts_path = resolve_facts_path(args.facts or project_root / "data" / "facts" / "facts.json")
    result = validate_facts_file(facts_path)

    errors = list(result.errors)
    warnings = list(result.warnings)

    if not errors:
        try:
            facts = load_facts(facts_path)
            fact_ids = {fact.id for fact in facts}
            fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json")
            for fragment in fragments.values():
                missing_sources = sorted(set(fragment.source_fact_ids).difference(fact_ids))
                if missing_sources:
                    errors.append(
                        f"fragment {fragment.fact_id}: unknown source_fact_ids: {', '.join(missing_sources)}"
                    )
                for level in ("A", "B"):
                    if not fragment.bullets.get(level):
                        errors.append(f"fragment {fragment.fact_id}: missing non-empty {level} bullets")
            profile = load_profile(project_root)
            if profile.confirmation not in {"candidate_asserted", "desensitized_sample"}:
                errors.append(
                    "profile confirmation must be candidate_asserted or desensitized_sample"
                )
            if not profile.awards:
                warnings.append("profile has no awards")
            if not profile.skills:
                errors.append("profile must contain at least one skill line")
            for skill in profile.skills:
                missing_sources = sorted(set(skill.source_fact_ids).difference(fact_ids))
                if missing_sources:
                    errors.append(
                        f"profile skill {skill.text}: unknown source_fact_ids: {', '.join(missing_sources)}"
                    )
                if not skill.source_fact_ids:
                    warnings.append(
                        f"profile skill omitted from generated resumes until fact-linked: {skill.text}"
                    )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"structured input validation failed: {exc}")

    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print(f"Facts file valid: {facts_path}")
        print("Resume fragments valid and source-linked.")
        print("Resume profile valid.")

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    return 1 if errors else 0


def run_generate(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    if args.file:
        jd_path = args.file
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.file.stem)
    else:
        jd_path = project_root / "data" / "jd_library" / f"{args.name}.md"
        if not jd_path.exists():
            print(f"Saved JD not found: {jd_path}", file=sys.stderr)
            return 2
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.name)

    review_path = args.review or project_root / "data" / "outputs" / output_slug / "review_sheet.md"
    if not review_path.exists():
        print(f"Review sheet not found: {review_path}", file=sys.stderr)
        print("Run `python3 backend/run_cli.py review --name <name>` first.", file=sys.stderr)
        return 2
    if not review_is_fresh(project_root, jd_path, review_path):
        print("Generate failed: review sheet is stale because the JD, facts, or fragments changed.", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py prepare --file {jd_path} --name {output_slug}` again.", file=sys.stderr)
        return 2
    pending_count = count_pending_review_items(review_path)
    if pending_count:
        print(f"Generate failed: review sheet still has {pending_count} pending item(s).", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py decide --name {output_slug}` first.", file=sys.stderr)
        return 2
    unverified_count = count_unverified_ab_items(review_path)
    if unverified_count:
        print(
            f"Generate failed: review sheet has {unverified_count} A/B item(s) without interactive confirmation markers.",
            file=sys.stderr,
        )
        print(f"Run `python3 backend/run_cli.py decide --name {output_slug}` and re-confirm them.", file=sys.stderr)
        return 2

    output_dir = project_root / "data" / "outputs" / output_slug
    try:
        tex_path = generate_resume_tex(
            jd_text=jd_text,
            review_path=review_path,
            output_dir=output_dir,
            project_root=project_root,
            matcher=args.matcher,
            use_llm_selection=args.llm_select,
        )
    except ModuleNotFoundError as exc:
        print(f"Generate failed: missing dependency for {args.matcher} matcher: {exc.name}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Generate failed: {exc}", file=sys.stderr)
        return 2

    print(f"Resume draft written: {tex_path}")
    return 0


def run_decide(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    output_slug = slugify(args.name)
    review_path = args.review or project_root / "data" / "outputs" / output_slug / "review_sheet.md"
    jd_path = project_root / "data" / "jd_library" / f"{output_slug}.md"
    if not review_path.exists():
        print(f"Review sheet not found: {review_path}", file=sys.stderr)
        print("Run `python3 backend/run_cli.py review --name <name>` first.", file=sys.stderr)
        return 2
    if not review_is_fresh(project_root, jd_path, review_path):
        print("Decide failed: review sheet is stale because the JD, facts, or fragments changed.", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py prepare --file {jd_path} --name {output_slug}` again.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("Decide failed: interactive review requires a real terminal (TTY).", file=sys.stderr)
        print("Do not pipe answers or drive this command through subprocess stdin.", file=sys.stderr)
        return 2
    code = run_interactive_decision(
        review_path,
        collect_notes=args.notes,
        project_root=project_root,
        revisit_all=args.revisit,
    )
    if code == 0:
        authorization_count = record_authorizations_from_review(project_root, review_path)
        if authorization_count:
            print(
                f"Reusable resume authorizations saved: {authorization_count} item(s) "
                "-> data/resume_authorizations.json"
            )
    return code


def run_prepare(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    jd_path, jd_text, name_hint = _read_jd_input(args, project_root)
    if not jd_text.strip():
        print("JD text is empty.", file=sys.stderr)
        return 2

    saved_jd_path = save_jd_memory(jd_text, project_root / "data" / "jd_library", name_hint=name_hint)
    output_slug = slugify(name_hint or saved_jd_path.stem)
    output_dir = project_root / "data" / "outputs" / output_slug

    result = analyze_jd(jd_text, matcher="keyword")
    report_path = write_report(render_markdown_report(result, saved_jd_path), output_dir)

    candidates = None
    candidate_note = None
    if args.semantic_candidates:
        candidates, candidate_note = _semantic_candidates_for_review(jd_text, result)
    review_path = write_review_sheet(
        render_review_sheet(
            result,
            saved_jd_path,
            semantic_candidates=candidates,
            semantic_note=candidate_note,
            project_root=project_root,
        ),
        output_dir,
    )
    reused_count = apply_reusable_authorizations(project_root, review_path)
    pending_count = count_pending_review_items(review_path)

    print(f"JD saved: {saved_jd_path}")
    print(f"Report written: {report_path}")
    print(f"Review sheet written: {review_path}")
    if reused_count:
        print(f"Reused {reused_count} unchanged resume authorization(s).")
    if pending_count:
        print(
            f"Next: python3 backend/run_cli.py decide --name {output_slug} "
            f"({pending_count} new or changed item(s) only)"
        )
        print(f"Then: python3 backend/run_cli.py finalize --name {output_slug} --llm-select --pdf")
    else:
        print("All matched wording already has reusable authorization.")
        print(f"Next: python3 backend/run_cli.py finalize --name {output_slug} --llm-select --pdf")
    if result.not_writable:
        print("Not writable:", ", ".join(result.not_writable))
    return 0


def run_finalize(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    try:
        _, jd_text, name_hint = _read_jd_input(args, project_root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output_slug = slugify(name_hint)
    review_path = args.review or project_root / "data" / "outputs" / output_slug / "review_sheet.md"
    if not review_path.exists():
        print(f"Review sheet not found: {review_path}", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py prepare --file <jd> --name {output_slug}` first.", file=sys.stderr)
        return 2
    jd_path = project_root / "data" / "jd_library" / f"{output_slug}.md" if not args.file else args.file
    if not review_is_fresh(project_root, jd_path, review_path):
        print("Finalize failed: review sheet is stale because the JD, facts, or fragments changed.", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py prepare --file {jd_path} --name {output_slug}` again.", file=sys.stderr)
        return 2
    pending_count = count_pending_review_items(review_path)
    if pending_count:
        print(f"Finalize failed: review sheet still has {pending_count} pending item(s).", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py decide --name {output_slug}` first.", file=sys.stderr)
        return 2
    unverified_count = count_unverified_ab_items(review_path)
    if unverified_count:
        print(
            f"Finalize failed: review sheet has {unverified_count} A/B item(s) without interactive confirmation markers.",
            file=sys.stderr,
        )
        print(f"Run `python3 backend/run_cli.py decide --name {output_slug}` and re-confirm them.", file=sys.stderr)
        return 2

    output_dir = project_root / "data" / "outputs" / output_slug
    try:
        tex_path = generate_resume_tex(
            jd_text=jd_text,
            review_path=review_path,
            output_dir=output_dir,
            project_root=project_root,
            matcher="keyword",
            use_llm_selection=args.llm_select,
        )
    except ValueError as exc:
        print(f"Finalize failed: {exc}", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py decide --name {output_slug}` first.", file=sys.stderr)
        return 2

    print(f"Resume draft written: {tex_path}")
    if args.pdf:
        try:
            _compile_pdf(tex_path)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


def run_status(args: argparse.Namespace) -> int:
    status = inspect_application(args.project_root, slugify(args.name))
    print(render_status(status))
    return 0


def run_list(args: argparse.Namespace) -> int:
    print(render_application_list(list_applications(args.project_root)))
    return 0


def run_record_outcome(args: argparse.Namespace) -> int:
    try:
        event = record_outcome(
            project_root=args.project_root,
            application=slugify(args.name),
            status=args.status,
            event_date=args.date,
            note=args.note,
        )
    except ValueError as exc:
        print(f"Record outcome failed: {exc}", file=sys.stderr)
        return 2
    digest = event.resume_sha256[:12] if event.resume_sha256 else "no-pdf"
    print(f"Outcome recorded: {event.date} {event.application} {event.status} resume={digest}")
    return 0


def run_list_outcomes(args: argparse.Namespace) -> int:
    print(render_outcomes(load_outcomes(default_outcome_path(args.project_root))))
    return 0


def run_deliver(args: argparse.Namespace) -> int:
    status = inspect_application(args.project_root, slugify(args.name))
    if not status.review_fresh or not status.tex_fresh or not status.pdf_fresh:
        print("Deliver failed: review or resume artifacts are stale.", file=sys.stderr)
        print(f"Next step: {status.next_step}", file=sys.stderr)
        return 2
    profile = load_profile(args.project_root)
    try:
        result = deliver_resume(
            project_root=args.project_root,
            name=slugify(args.name),
            company=args.company,
            role=args.role,
            delivery_root=args.delivery_root,
            candidate=args.candidate or profile.name,
            school=args.school or profile.education.school,
            major=args.major or profile.education.major.removesuffix("专业"),
            include_tex=not args.no_tex,
        )
    except FileNotFoundError as exc:
        print(f"Deliver failed: {exc}", file=sys.stderr)
        print(f"Run `python3 backend/run_cli.py finalize --name {slugify(args.name)} --pdf` first.", file=sys.stderr)
        return 2
    except DeliveryQualityError as exc:
        print("Deliver failed: export gate is not satisfied.", file=sys.stderr)
        print(render_quality(exc.quality), file=sys.stderr)
        return 2
    print(f"Delivered PDF: {result.pdf_path}")
    if result.tex_path:
        print(f"Delivered TeX: {result.tex_path}")
    return 0


def run_explain_jd(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    if args.file:
        jd_path = args.file
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.file.stem)
    else:
        jd_path = project_root / "data" / "jd_library" / f"{args.name}.md"
        if not jd_path.exists():
            print(f"Saved JD not found: {jd_path}", file=sys.stderr)
            return 2
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.name)

    try:
        result = analyze_jd(jd_text, matcher=args.matcher)
    except ModuleNotFoundError as exc:
        print(f"explain-jd failed: missing dependency for {args.matcher} matcher: {exc.name}", file=sys.stderr)
        return 2

    data = build_jd_insight_data(jd_path, jd_text, result, use_llm=not args.no_llm)
    print(render_markdown(data))
    if args.write:
        output_dir = project_root / "data" / "outputs" / output_slug
        md_path, html_path = write_jd_insight(output_dir, data)
        print(f"\nJD insight report written: {md_path}")
        print(f"JD insight HTML written: {html_path}")
    return 0


def run_gap_check(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    if args.file:
        jd_path = args.file
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.file.stem)
    else:
        jd_path = project_root / "data" / "jd_library" / f"{args.name}.md"
        if not jd_path.exists():
            print(f"Saved JD not found: {jd_path}", file=sys.stderr)
            return 2
        jd_text = jd_path.read_text(encoding="utf-8")
        output_slug = slugify(args.name)

    try:
        result = analyze_jd(jd_text, matcher=args.matcher)
    except ModuleNotFoundError as exc:
        print(f"gap-check failed: missing dependency for {args.matcher} matcher: {exc.name}", file=sys.stderr)
        return 2

    data = build_jd_insight_data(jd_path, jd_text, result, use_llm=False)
    print(render_gap_warning(data))
    if args.write:
        output_dir = project_root / "data" / "outputs" / output_slug
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "gap_warning.md"
        path.write_text(render_gap_warning(data), encoding="utf-8")
        html_path = output_dir / "gap_warning.html"
        html_path.write_text(render_gap_warning_html(data), encoding="utf-8")
        print(f"\nGap warning written: {path}")
        print(f"Gap warning HTML written: {html_path}")
    return 0


def run_career_trends(args: argparse.Namespace) -> int:
    project_root: Path = args.project_root
    jd_dir = project_root / "data" / "jd_library"
    jd_paths = sorted(jd_dir.glob("*.md"))
    if not jd_paths:
        print(f"No JD files found in {jd_dir}", file=sys.stderr)
        return 2

    tech_jds: dict[str, set[str]] = {}
    for jd_path in jd_paths:
        try:
            jd_text = jd_path.read_text(encoding="utf-8")
            result = analyze_jd(jd_text, matcher="keyword")
            data = build_jd_insight_data(jd_path, jd_text, result, use_llm=False)
        except Exception as exc:  # skip a broken JD, keep the rest
            print(f"Skipping {jd_path.name}: {exc}", file=sys.stderr)
            continue
        for tech in data.not_writable:
            tech_jds.setdefault(tech, set()).add(jd_path.stem)

    total = len(jd_paths)
    ranked = sorted(tech_jds.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    high = [(t, j) for t, j in ranked if len(j) >= 2]
    low = [(t, j) for t, j in ranked if len(j) == 1]

    lines = [f"# 跨 JD 缺口预警（共 {total} 份 JD）", ""]
    lines.append("## 🔴 优先补（≥2 份 JD 提到，事实库无证据）")
    lines.append("")
    if high:
        for tech, jds in high:
            lines.append(f"- **{tech}**（{len(jds)} 份：{', '.join(sorted(jds))}）")
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 🟡 考虑补（1 份提到）")
    lines.append("")
    if low:
        for tech, jds in low:
            lines.append(f"- {tech}（{', '.join(sorted(jds))}）")
    else:
        lines.append("- 无")
    lines.append("")
    report = "\n".join(lines)
    print(report)

    history = load_snapshots(project_root)
    diff = diff_against_last(tech_jds, history)
    if history:
        print(f"\n## 📈 与上次快照对比")
        print("")
        if diff.added:
            print(f"新增缺口: {', '.join(diff.added)}")
        else:
            print("新增缺口: 无")
        if diff.resolved:
            print(f"已补齐/消失: {', '.join(diff.resolved)}")
        else:
            print("已补齐/消失: 无")
        print("")

    snapshot = record_gap_snapshot(project_root, tech_jds)
    print(f"Snapshot recorded: {snapshot.date} ({len(snapshot.gaps)} gap techs)")

    if args.write:
        out = project_root / "data" / "evaluation" / "career_trends.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"\nWritten: {out}")
    return 0


def run_gaps(args: argparse.Namespace) -> int:
    name = slugify(args.name)
    report = build_gap_report(args.project_root, name)
    print(render_gap_report(report))
    if args.write:
        path = write_gap_report(report, args.project_root / "data" / "outputs" / name)
        print(f"Gap report written: {path}")
    return 0


def run_expand_review(args: argparse.Namespace) -> int:
    name = slugify(args.name)
    try:
        result = expand_review_from_gaps(args.project_root, name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Review sheet updated: {result.review_path}")
    print(f"Gap candidates added: {result.added}")
    if result.added_fact_ids:
        print("Added fact_ids:", ", ".join(result.added_fact_ids))
    if result.skipped_existing:
        print(f"Skipped existing fact_ids: {result.skipped_existing}")
    print(f"Next: python3 backend/run_cli.py decide --name {name}")
    return 0


def run_record_interview(args: argparse.Namespace) -> int:
    name = slugify(args.name)
    try:
        feedback = record_feedback(
            project_root=args.project_root,
            application=name,
            fact_id=args.fact_id,
            question=args.question,
            note=args.note,
            event_date=args.date,
        )
    except ValueError as exc:
        print(f"Record interview failed: {exc}", file=sys.stderr)
        return 2
    print(f"Feedback recorded: {feedback.date} [{feedback.fact_id}] {feedback.question}")
    if args.append_boundary:
        if not args.note:
            print("--append-boundary requires --note; skipped boundary write.", file=sys.stderr)
            return 2
        facts_path = resolve_facts_path(args.project_root / "data" / "facts" / "facts.json")
        result = append_boundary_to_facts(facts_path, args.fact_id, args.note)
        if result == "written":
            print(f"Boundary appended to fact {args.fact_id} in {facts_path}")
        elif result == "duplicate":
            print(f"Boundary already exists for fact {args.fact_id}; skipped.")
        elif result == "fact_not_found":
            print(f"Fact not found in {facts_path}: {args.fact_id}", file=sys.stderr)
            return 2
        else:
            print(f"Facts file not found: {facts_path}", file=sys.stderr)
            return 2
    return 0


def run_list_interview(args: argparse.Namespace) -> int:
    name = slugify(args.name)
    items = load_feedback(args.project_root, name)
    print(render_feedback(items))
    return 0


def run_mastery_history(args: argparse.Namespace) -> int:
    history = load_mastery_history(args.project_root)
    print(render_mastery_history(history, fact_id=args.fact_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return run_analyze(args)
    if args.command == "review":
        return run_review(args)
    if args.command == "validate":
        return run_validate(args)
    if args.command == "generate":
        return run_generate(args)
    if args.command == "decide":
        return run_decide(args)
    if args.command == "prepare":
        return run_prepare(args)
    if args.command == "finalize":
        return run_finalize(args)
    if args.command == "status":
        return run_status(args)
    if args.command == "list":
        return run_list(args)
    if args.command == "record-outcome":
        return run_record_outcome(args)
    if args.command == "list-outcomes":
        return run_list_outcomes(args)
    if args.command == "deliver":
        return run_deliver(args)
    if args.command == "explain-jd":
        return run_explain_jd(args)
    if args.command == "gaps":
        return run_gaps(args)
    if args.command == "gap-check":
        return run_gap_check(args)
    if args.command == "career-trends":
        return run_career_trends(args)
    if args.command == "expand-review":
        return run_expand_review(args)
    if args.command == "record-interview":
        return run_record_interview(args)
    if args.command == "list-interview":
        return run_list_interview(args)
    if args.command == "mastery-history":
        return run_mastery_history(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

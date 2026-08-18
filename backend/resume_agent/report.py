from __future__ import annotations

from pathlib import Path

from .analyzer import AnalysisResult


def render_markdown_report(result: AnalysisResult, jd_path: Path) -> str:
    lines: list[str] = []
    lines.append("# Match Report")
    lines.append("")
    lines.append(f"- JD source: `{jd_path}`")
    lines.append(f"- Inferred job type: **{result.job_type}**")
    lines.append("")

    lines.append("## Higher Retrieval Signals (Not Resume Priority)")
    if result.strong_matches:
        for match in result.strong_matches:
            lines.append(f"- **{match.fact.title}**")
            lines.append(f"  - Matched keywords: {', '.join(match.matched_keywords)}")
            lines.append(f"  - Evidence: {match.fact.summary}")
            lines.append(f"  - Boundary: {'; '.join(match.fact.boundaries)}")
            lines.append(f"  - Risk: {match.fact.risk}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Lower Retrieval Signals (Not Resume Priority)")
    if result.weak_matches:
        for match in result.weak_matches:
            lines.append(f"- **{match.fact.title}**: matched {', '.join(match.matched_keywords)}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Not Writable")
    if result.not_writable:
        for tech, reason in result.not_writable.items():
            lines.append(f"- **{tech}**: {reason}")
    else:
        lines.append("- None detected")
    lines.append("")

    lines.append("## Resume Strategy")
    for item in result.recommendations:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Interview Risks")
    if result.risks:
        for risk in result.risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- No major risks detected by current rules.")
    lines.append("")

    lines.append("## Manual Review Checklist")
    lines.append("- Does each suggested bullet have fact-bank evidence?")
    lines.append("- Does any line imply production, scale, or ownership beyond the fact bank?")
    lines.append("- Can the candidate explain each retained bullet in two minutes?")
    lines.append("- Does retrieval signal differ from the experience's actual resume value?")
    return "\n".join(lines) + "\n"


def write_report(report: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "match_report.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path

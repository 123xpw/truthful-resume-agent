from __future__ import annotations

from pathlib import Path
import re

from .analyzer import MatcherName, analyze_jd
from .authorization_store import load_valid_authorizations
from .fact_store import load_facts
from .fragments import ResumeFragment, load_fragments
from .profile import ResumeProfile, SkillProfile, load_profile
from .review_parser import parse_review_mastery
from .selection import build_selection_plan, write_selection_report


FORMAT_PREFIX = r"""% !TEX program = xelatex
% ==================== 格式设置区开始：定制简历时禁止修改 ====================
\documentclass[10.5pt,a4paper]{article}
\usepackage[left=1.15cm,right=1.15cm,top=0.78cm,bottom=0.78cm]{geometry}
\usepackage{fontspec}
\usepackage{xeCJK}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{tabularx}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{needspace}
\usepackage{titlesec}
\usepackage{ragged2e}

\IfFontExistsTF{Arial}{\setmainfont{Arial}}{\setmainfont{Helvetica}}
\IfFontExistsTF{Noto Sans CJK SC}{\setCJKmainfont{Noto Sans CJK SC}}{
  \IfFontExistsTF{PingFang SC}{\setCJKmainfont{PingFang SC}}{
    \IfFontExistsTF{Hiragino Sans GB}{\setCJKmainfont{Hiragino Sans GB}}{\setCJKmainfont{Songti SC}}
  }
}

\definecolor{AccentBlue}{HTML}{2F5597}
\definecolor{TextBlack}{HTML}{202124}
\definecolor{MutedText}{HTML}{555555}
\definecolor{RuleGray}{HTML}{D8DDE6}

\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\setlength{\tabcolsep}{0pt}
\renewcommand{\arraystretch}{1.0}
\linespread{1.02}
\setlength{\emergencystretch}{2em}
\AtBeginDocument{\color{TextBlack}}

\titleformat{\section}
  {\fontsize{14}{16}\selectfont\bfseries\color{TextBlack}}
  {}{0pt}{}
  [\vspace{-6pt}{\color{AccentBlue}\rule{2.75cm}{2.4pt}}\vspace{-4pt}]
\titlespacing*{\section}{0pt}{6pt}{5pt}

\newcommand{\resumeName}[1]{%
  {\fontsize{25}{28}\selectfont\bfseries #1}
}

\newcommand{\contactInfo}[3]{%
  \begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}lll@{}}
    {\small #1} & {\small #2} & {\small 电子邮箱：\href{mailto:#3}{#3}}
  \end{tabular*}
}

\newcommand{\resumeHeader}[5]{%
  \begin{tabularx}{\textwidth}{@{}Xr@{}}
    \begin{minipage}[t]{0.76\textwidth}
      \vspace{0pt}
      \resumeName{#1}\\[5pt]
      \contactInfo{#2}{#3}{#4}
    \end{minipage}
    &
    \begin{minipage}[t]{1.78cm}
      \vspace{0pt}
      \IfFileExists{#5}{\includegraphics[width=1.72cm,height=2.22cm,keepaspectratio]{#5}}{}
    \end{minipage}
  \end{tabularx}
  \vspace{-10pt}
}

\newcommand{\optionalLink}[2]{%
  \if\relax\detokenize{#2}\relax
  \else
    \if\relax\detokenize{#1}\relax
      #2%
    \else
      \href{#1}{#2}%
    \fi
  \fi
}

\newcommand{\eduLine}[5]{%
  #1\hspace{1.45em}#2\hspace{1.45em}#3\hspace{1.45em}#4\par
  \if\relax\detokenize{#5}\relax\else{\small\color{MutedText}#5}\fi
  \vspace{5pt}
}

\newcommand{\entry}[3]{%
  \Needspace{4\baselineskip}
  \begin{tabularx}{\textwidth}{@{}p{3.05cm}X>{\raggedleft\arraybackslash}p{4.35cm}@{}}
    \textbf{#1} & \textbf{#2} & \textbf{#3}
  \end{tabularx}
  \vspace{-3pt}
}

\newcommand{\projectEntry}[4]{%
  \Needspace{4\baselineskip}
  \begin{tabularx}{\textwidth}{@{}p{3.05cm}X>{\raggedleft\arraybackslash}p{4.35cm}@{}}
    \textbf{#1} & \textbf{#2} & {\small\optionalLink{#4}{#3}}
  \end{tabularx}
  \vspace{-3pt}
}

\newcommand{\projectEntryUrl}[4]{%
  \Needspace{4\baselineskip}
  \begin{tabularx}{\textwidth}{@{}p{3.05cm}X>{\raggedleft\arraybackslash}p{6.3cm}@{}}
    \textbf{#1} & \textbf{#2} & {\scriptsize\href{#4}{#3}}
  \end{tabularx}
  \vspace{-3pt}
}

\newenvironment{resumeItems}{%
  \begin{itemize}[leftmargin=1.1em,label=\textbullet,itemsep=1.8pt,topsep=1.8pt,parsep=0pt,partopsep=0pt]
  \small\RaggedRight
}{\end{itemize}\vspace{1.6pt}}

\newcommand{\keywords}[1]{{\small\RaggedRight\textbf{技术关键词：}#1\par}\vspace{1.5pt}}
\newcommand{\sectionRule}{\vspace{1.6pt}{\color{RuleGray}\hrule height 0.45pt}\vspace{4.5pt}}
% ==================== 格式设置区结束：定制简历时禁止修改 ====================

\begin{document}
% ==================== 内容区开始：定制简历时只允许修改本区 ====================
"""


FORMAT_SUFFIX = r"""% ==================== 内容区结束：定制简历时只允许修改本区 ====================
\end{document}
"""


from .layout_config import (
    COMPOSITE_INTERNSHIP_SOURCES,
    INTERNSHIP_EXCLUSIVE_GROUPS,
    resolve_internship_order,
    resolve_project_order,
)


def _latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _escape_unescaped_percent(value: str) -> str:
    """Preserve fragment LaTeX while preventing bare percent signs from commenting out text."""
    return re.sub(r"(?<!\\)%", r"\\%", value)


def _fragment_level(fragment: ResumeFragment, mastery: dict[str, str]) -> str | None:
    levels = [mastery.get(fact_id) for fact_id in fragment.source_fact_ids]
    if not levels or any(level not in {"A", "B"} for level in levels):
        return None
    return "A" if all(level == "A" for level in levels) else "B"


def _eligible_fragment_levels(
    fragments: dict[str, ResumeFragment],
    review_mastery: dict[str, str],
    global_authorizations: dict[str, dict],
) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """Build the complete authorized inventory; matcher results do not gate it."""
    levels: dict[str, str] = {}
    excluded: list[tuple[str, str, str]] = []
    for fragment_id, fragment in fragments.items():
        current_level = _fragment_level(fragment, review_mastery)
        if current_level in {"A", "B"}:
            levels[fragment_id] = current_level
            continue
        current_source_levels = [review_mastery.get(fact_id) for fact_id in fragment.source_fact_ids]
        if any(level in {"C", "D"} for level in current_source_levels):
            excluded.append((fragment_id, fragment.title, "current review explicitly blocks one or more source facts (C/D)"))
            continue
        authorization = global_authorizations.get(fragment_id, {})
        authorized_level = authorization.get("level")
        if authorized_level in {"A", "B"}:
            levels[fragment_id] = str(authorized_level)
            continue
        if authorized_level in {"C", "D"}:
            reason = f"global wording authorization is {authorized_level}"
        elif fragment_id in global_authorizations:
            reason = "global authorization is invalid"
        else:
            reason = "no current content-hash-valid A/B authorization"
        excluded.append((fragment_id, fragment.title, reason))
    return levels, excluded


def _suppressed_internship_exclusions(
    authorized_ids: set[str],
    visible_ids: list[str],
    fragments: dict[str, ResumeFragment],
) -> list[tuple[str, str, str]]:
    visible = set(visible_ids)
    exclusions: list[tuple[str, str, str]] = []
    for fragment_id in sorted(authorized_ids - visible):
        representative = next(
            (
                candidate
                for candidate in visible_ids
                if fragment_id in COMPOSITE_INTERNSHIP_SOURCES.get(candidate, set())
                or any(fragment_id in group and candidate in group for group in INTERNSHIP_EXCLUSIVE_GROUPS)
            ),
            None,
        )
        reason = (
            f"same internship stint is represented by {representative}"
            if representative
            else "suppressed by internship representation rules"
        )
        exclusions.append((fragment_id, fragments[fragment_id].title, reason))
    return exclusions


def _select_profile_skills(
    profile: ResumeProfile,
    mastery: dict[str, str],
    known_fact_ids: set[str],
) -> tuple[list[SkillProfile], list[tuple[SkillProfile, str]]]:
    included: list[SkillProfile] = []
    omitted: list[tuple[SkillProfile, str]] = []
    for skill in profile.skills:
        if not skill.source_fact_ids:
            omitted.append((skill, "no source_fact_ids"))
            continue
        unknown = [fact_id for fact_id in skill.source_fact_ids if fact_id not in known_fact_ids]
        if unknown:
            omitted.append((skill, f"unknown fact IDs: {', '.join(unknown)}"))
            continue
        unconfirmed = [
            fact_id for fact_id in skill.source_fact_ids if mastery.get(fact_id) not in {"A", "B"}
        ]
        if unconfirmed:
            omitted.append((skill, f"facts not A/B-confirmed for this JD: {', '.join(unconfirmed)}"))
            continue
        included.append(skill)
    return included, omitted


def _append_skill_selection_report(
    report_path: Path,
    included: list[SkillProfile],
    omitted: list[tuple[SkillProfile, str]],
) -> None:
    lines = ["", "## Profile Skills", "", "### Included"]
    if included:
        lines.extend(
            f"- {skill.text} (sources: {', '.join(skill.source_fact_ids)})"
            for skill in included
        )
    else:
        lines.append("- None")
    lines.extend(["", "### Omitted"])
    if omitted:
        lines.extend(f"- {skill.text}: {reason}" for skill, reason in omitted)
    else:
        lines.append("- None")
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _section_entry(fragment: ResumeFragment, level: str) -> str:
    lines: list[str] = []
    if fragment.entry_type == "internship":
        lines.append(f"\\entry{{{fragment.date}}}{{{fragment.title}}}{{{fragment.organization}}}")
    elif fragment.entry_type == "project_url":
        lines.append(
            f"\\projectEntryUrl{{{fragment.date}}}{{{fragment.title}}}{{{fragment.url_text}}}{{{fragment.url}}}"
        )
    else:
        lines.append(f"\\projectEntry{{{fragment.date}}}{{{fragment.title}}}{{}}{{}}")

    lines.append("\\begin{resumeItems}")
    for bullet in fragment.bullets[level]:
        lines.append(f"  \\item {_escape_unescaped_percent(bullet)}")
    lines.append("\\end{resumeItems}")
    if fragment.keywords:
        lines.append(f"\\keywords{{{fragment.keywords}}}")
    return "\n".join(lines)


def _choose_project_order(job_type: str, selected_ids: set[str], fragments: dict[str, ResumeFragment]) -> list[str]:
    order = resolve_project_order(job_type, fragments)
    preferred = [fact_id for fact_id in order if fact_id in selected_ids]
    return [*preferred, *sorted(selected_ids - set(preferred))]


def _choose_internship_order(selected_ids: set[str], fragments: dict[str, ResumeFragment]) -> list[str]:
    suppressed_sources: set[str] = set()
    for composite_id, source_ids in COMPOSITE_INTERNSHIP_SOURCES.items():
        if composite_id in selected_ids:
            suppressed_sources.update(source_ids)
    for group in INTERNSHIP_EXCLUSIVE_GROUPS:
        selected_group = [fact_id for fact_id in group if fact_id in selected_ids]
        if selected_group:
            suppressed_sources.update(selected_group[1:])
    order = resolve_internship_order(fragments)
    preferred = [
        fact_id
        for fact_id in order
        if fact_id in selected_ids and fact_id not in suppressed_sources
    ]
    remaining = selected_ids - set(preferred) - suppressed_sources
    return [*preferred, *sorted(remaining)]


def generate_resume_tex(
    jd_text: str,
    review_path: Path,
    output_dir: Path,
    project_root: Path,
    matcher: MatcherName = "keyword",
    use_llm_selection: bool = False,
) -> Path:
    result = analyze_jd(jd_text, matcher=matcher)
    mastery = parse_review_mastery(review_path)
    fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json")
    profile = load_profile(project_root)

    valid_authorizations = load_valid_authorizations(project_root)
    selected_levels, excluded_items = _eligible_fragment_levels(
        fragments,
        mastery,
        valid_authorizations,
    )

    if not selected_levels:
        raise ValueError("No A/B facts selected. Update review_sheet.md before generating resume.")

    internship_ids = {
        fact_id for fact_id in selected_levels if fragments[fact_id].section == "实习经历"
    }
    eligible_internships = _choose_internship_order(internship_ids, fragments)
    excluded_items.extend(
        _suppressed_internship_exclusions(internship_ids, eligible_internships, fragments)
    )
    project_ids = {
        fact_id for fact_id in selected_levels if fragments[fact_id].section == "项目经历"
    }
    eligible_projects = _choose_project_order(result.job_type, project_ids, fragments)
    eligible_order = [*eligible_internships, *eligible_projects]
    facts = {fact.id: fact for fact in load_facts(project_root / "data" / "facts" / "facts.json")}
    represented_fact_ids = {
        source_fact_id
        for fragment in fragments.values()
        for source_fact_id in fragment.source_fact_ids
    }
    for fact_id in sorted(set(facts) - represented_fact_ids):
        excluded_items.append(
            (fact_id, facts[fact_id].title, "fact exists but has no source-linked resume fragment")
        )
    selection = build_selection_plan(
        jd_text=jd_text,
        ordered_ids=eligible_order,
        selected_levels=selected_levels,
        fragments=fragments,
        facts=facts,
        result=result,
        use_llm=use_llm_selection,
        excluded_items=tuple(sorted(set(excluded_items))),
    )
    selected_fragment_ids = set(selection.selected_ids)
    selection_report_path = write_selection_report(selection, fragments, output_dir)
    effective_mastery = dict(mastery)
    for fragment_id, level in selected_levels.items():
        for fact_id in fragments[fragment_id].source_fact_ids:
            effective_mastery.setdefault(fact_id, level)
    included_skills, omitted_skills = _select_profile_skills(profile, effective_mastery, set(facts))
    _append_skill_selection_report(selection_report_path, included_skills, omitted_skills)

    lines: list[str] = [FORMAT_PREFIX]
    lines.append(f"% Profile source: {profile.source_path.relative_to(project_root)} ({profile.confirmation})")
    lines.append(
        "\\resumeHeader"
        f"{{{_latex_escape(profile.name)}}}"
        f"{{出生年月：{_latex_escape(profile.birth)}}}"
        f"{{电话号码：{_latex_escape(profile.phone)}}}"
        f"{{{_latex_escape(profile.email)}}}"
        "{photo.jpeg}"
    )
    lines.append("")
    lines.append("\\section{教育背景}")
    lines.append(
        "\\eduLine"
        f"{{{_latex_escape(profile.education.date)}}}"
        f"{{{_latex_escape(profile.education.school)}}}"
        f"{{{_latex_escape(profile.education.major)}}}"
        f"{{{_latex_escape(profile.education.details)}}}"
        "{}"
    )
    lines.append("")

    lines.append("\\section{实习经历}")
    first = True
    for fact_id in eligible_internships:
        if fact_id not in selected_fragment_ids:
            continue
        if not first:
            lines.append("\\sectionRule")
        lines.append(_section_entry(fragments[fact_id], selected_levels[fact_id]))
        first = False
    lines.append("")

    selected_projects = [fact_id for fact_id in eligible_projects if fact_id in selected_fragment_ids]
    if selected_projects:
        lines.append("\\section{项目经历}")
        for index, fact_id in enumerate(selected_projects):
            if index:
                lines.append("\\sectionRule")
            lines.append(_section_entry(fragments[fact_id], selected_levels[fact_id]))
        lines.append("")

    lines.append("\\section{荣誉奖项与证书}")
    lines.append("\\begin{resumeItems}")
    for award in profile.awards:
        lines.append(f"  \\item {_latex_escape(award)}")
    lines.append("\\end{resumeItems}")
    lines.append("")
    lines.append("\\section{专业技能}")
    lines.append("\\begin{resumeItems}")
    for skill in included_skills:
        lines.append(f"  \\item {_latex_escape(skill.text)}")
    lines.append("\\end{resumeItems}")
    lines.append(FORMAT_SUFFIX)

    output_dir.mkdir(parents=True, exist_ok=True)
    photo_link = output_dir / "photo.jpeg"
    source_photo = (project_root / profile.photo_source).resolve() if profile.photo_source else None
    if source_photo is not None and source_photo.exists() and not photo_link.exists():
        photo_link.symlink_to(source_photo)

    output_path = output_dir / "resume_draft.tex"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path

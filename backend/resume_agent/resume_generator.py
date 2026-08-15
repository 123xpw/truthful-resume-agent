from __future__ import annotations

from pathlib import Path

from .analyzer import MatcherName, analyze_jd
from .fragments import ResumeFragment, load_fragments
from .profile import load_profile
from .review_parser import parse_review_mastery


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


INTERNSHIP_ORDER = (
    "intern_optimization_ai_coding",
    "intern_data_automation",
    "intern_csharp_ai_mvp",
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


def _fragment_level(fragment: ResumeFragment, mastery: dict[str, str]) -> str | None:
    levels = [mastery.get(fact_id) for fact_id in fragment.source_fact_ids]
    if not levels or any(level not in {"A", "B"} for level in levels):
        return None
    return "A" if all(level == "A" for level in levels) else "B"


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
        lines.append(f"  \\item {bullet}")
    lines.append("\\end{resumeItems}")
    if fragment.keywords:
        lines.append(f"\\keywords{{{fragment.keywords}}}")
    return "\n".join(lines)


def _choose_project_order(job_type: str, selected_ids: set[str]) -> list[str]:
    ai_order = [
        "project_truthful_resume_agent",
        "project_emotion_pixel_eval",
        "project_chinese_learning_mvp",
        "project_dl_learning_lab",
    ]
    algo_order = [
        "project_emotion_pixel_eval",
        "project_dl_learning_lab",
        "project_chinese_learning_mvp",
    ]
    order = algo_order if job_type == "Algorithm / multimodal research" else ai_order
    project_ids = [fact_id for fact_id in order if fact_id in selected_ids]
    if job_type == "AI application / Agent engineering":
        return project_ids[:2]
    return project_ids


def generate_resume_tex(
    jd_text: str,
    review_path: Path,
    output_dir: Path,
    project_root: Path,
    matcher: MatcherName = "keyword",
) -> Path:
    result = analyze_jd(jd_text, matcher=matcher)
    mastery = parse_review_mastery(review_path)
    fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json")
    profile = load_profile(project_root)

    selected_levels: dict[str, str] = {
        fact_id: level
        for fact_id, fragment in fragments.items()
        if (level := _fragment_level(fragment, mastery)) in {"A", "B"}
    }

    if not selected_levels:
        raise ValueError("No A/B facts selected. Update review_sheet.md before generating resume.")

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
    for fact_id in INTERNSHIP_ORDER:
        if fact_id not in selected_levels:
            continue
        if not first:
            lines.append("\\sectionRule")
        lines.append(_section_entry(fragments[fact_id], selected_levels[fact_id]))
        first = False
    lines.append("")

    project_ids = _choose_project_order(result.job_type, set(selected_levels))
    if project_ids:
        lines.append("\\section{项目经历}")
        for index, fact_id in enumerate(project_ids):
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
    for skill in profile.skills:
        lines.append(f"  \\item {_latex_escape(skill)}")
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

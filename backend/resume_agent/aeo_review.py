"""AEO-first review of the actual resume an automated screener will read.

The deterministic layer checks explicit JD-term coverage against the rendered
resume text and the fact bank. The optional LLM layer simulates how a screening
model may describe the candidate, identify red flags, and misread bullets. Its
output is advisory and never enters resume generation.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
from pathlib import Path
import re

from .analyzer import analyze_jd
from .fact_store import load_facts
from .llm_client import LLMNotConfigured, chat_completion
from .rules import NOT_WRITABLE_TECH, has_fact_evidence, term_matches


@dataclass(frozen=True)
class AEOTermCoverage:
    term: str
    in_resume: bool
    fact_supported: bool
    status: str


@dataclass(frozen=True)
class AEOReview:
    jd_path: Path
    resume_path: Path
    job_type: str
    coverage: tuple[AEOTermCoverage, ...]
    deterministic_red_flags: tuple[str, ...]
    persona: str | None
    red_flags: tuple[str, ...]
    business_problem_fit: tuple[str, ...]
    likely_misreadings: tuple[str, ...]
    rewrite_priorities: tuple[str, ...]
    llm_error: str | None


def latex_to_screening_text(tex: str) -> str:
    """Best-effort plain text close to what a PDF text extractor will expose."""
    value = re.sub(r"(?m)%.*$", "", tex)
    value = re.sub(r"\\(?:href|optionalLink)\{[^{}]*\}\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z@]+(?:\[[^\]]*\])?", " ", value)
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\{([^{}]*)\}", r" \1 ", value)
    value = value.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&")
    value = re.sub(r"[\\{}]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def build_term_coverage(jd_text: str, resume_text: str) -> tuple[AEOTermCoverage, ...]:
    facts = list(load_facts())
    terms: set[str] = set(NOT_WRITABLE_TECH)
    for fact in facts:
        terms.update(fact.keywords)
    mentioned = sorted(
        (term for term in terms if len(term.strip()) >= 2 and term_matches(jd_text, term)),
        key=lambda value: (value.lower(), value),
    )
    coverage: list[AEOTermCoverage] = []
    for term in mentioned:
        in_resume = term_matches(resume_text, term)
        supported = has_fact_evidence(term, facts)
        if in_resume and supported:
            status = "supported_explicit"
        elif in_resume:
            status = "unsupported_present"
        elif supported:
            status = "supported_but_missing"
        else:
            status = "unsupported_do_not_add"
        coverage.append(AEOTermCoverage(term, in_resume, supported, status))
    return tuple(coverage)


def _parse_llm_review(content: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)
    required = {
        "persona": str,
        "red_flags": list,
        "business_problem_fit": list,
        "likely_misreadings": list,
        "rewrite_priorities": list,
    }
    if not isinstance(parsed, dict):
        raise ValueError("AEO response is not an object")
    for field, expected_type in required.items():
        if not isinstance(parsed.get(field), expected_type):
            raise ValueError(f"AEO response field has invalid type: {field}")
    for field in required:
        if field == "persona":
            continue
        if not all(isinstance(item, str) and item.strip() for item in parsed[field]):
            raise ValueError(f"AEO response contains invalid list items: {field}")
    return parsed


def _llm_aeo_review(jd_text: str, resume_text: str) -> tuple[dict | None, str | None]:
    prompt = (
        "你是第一轮自动化简历筛选模型。只根据给出的 JD 和简历文本判断，不补充候选人没有写出的经历。"
        "目标不是润色，而是暴露 AI 会如何理解这份简历。严格输出 JSON，字段如下："
        "persona（这份简历呈现的人设，一段话）；red_flags（招聘经理视角的风险）；"
        "business_problem_fit（候选人能解决/尚不能证明能解决的岗位业务问题）；"
        "likely_misreadings（简历 bullet 可能造成的错误理解）；"
        "rewrite_priorities（最需要澄清的表达方向，不得建议虚构技术或成绩）。"
        "每个数组最多 5 项。不要输出 JSON 之外的内容。\n\n"
        f"JD：\n{jd_text}\n\n简历：\n{resume_text}"
    )
    try:
        return _parse_llm_review(chat_completion([{"role": "user", "content": prompt}], temperature=0.0)), None
    except LLMNotConfigured as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def build_aeo_review(
    jd_path: Path,
    resume_path: Path,
    use_llm: bool = True,
) -> AEOReview:
    jd_text = jd_path.read_text(encoding="utf-8")
    resume_text = latex_to_screening_text(resume_path.read_text(encoding="utf-8"))
    analysis = analyze_jd(jd_text, matcher="keyword")
    coverage = build_term_coverage(jd_text, resume_text)
    deterministic_red_flags: list[str] = []
    unsupported_present = [item.term for item in coverage if item.status == "unsupported_present"]
    if unsupported_present:
        deterministic_red_flags.append(
            "简历出现事实库尚无证据的 JD 术语：" + "、".join(unsupported_present)
        )
    if not coverage:
        deterministic_red_flags.append("未从 JD 与事实关键词集合中提取到可比较术语，需要人工检查 JD 格式。")

    llm_data = None
    llm_error = None
    if use_llm:
        llm_data, llm_error = _llm_aeo_review(jd_text, resume_text)
    return AEOReview(
        jd_path=jd_path,
        resume_path=resume_path,
        job_type=analysis.job_type,
        coverage=coverage,
        deterministic_red_flags=tuple(deterministic_red_flags),
        persona=llm_data["persona"] if llm_data else None,
        red_flags=tuple(llm_data["red_flags"]) if llm_data else (),
        business_problem_fit=tuple(llm_data["business_problem_fit"]) if llm_data else (),
        likely_misreadings=tuple(llm_data["likely_misreadings"]) if llm_data else (),
        rewrite_priorities=tuple(llm_data["rewrite_priorities"]) if llm_data else (),
        llm_error=llm_error,
    )


def render_aeo_markdown(review: AEOReview) -> str:
    lines = [
        "# AEO Resume Review",
        "",
        f"- JD: `{review.jd_path}`",
        f"- actual resume: `{review.resume_path}`",
        f"- inferred job type: **{review.job_type}**",
        "- rule sections are deterministic; AI-screening interpretation is advisory.",
        "",
        "## 1. AI 识别的人设",
        "",
        review.persona or f"_AI 筛选模拟不可用：{review.llm_error or 'disabled'}_",
        "",
        "## 2. 招聘经理视角 Red Flags",
        "",
    ]
    combined_flags = [*review.deterministic_red_flags, *review.red_flags]
    lines.extend(f"- {item}" for item in combined_flags or ["未发现明确 red flag。"])
    lines.extend(["", "## 3. 岗位业务问题匹配", ""])
    lines.extend(f"- {item}" for item in review.business_problem_fit or ["AI 筛选模拟未生成该部分。"])
    lines.extend(["", "## 4. 可能的 AI 误读", ""])
    lines.extend(f"- {item}" for item in review.likely_misreadings or ["AI 筛选模拟未生成该部分。"])
    lines.extend(["", "## 5. JD 术语覆盖与事实边界", ""])
    lines.extend(["| Term | In resume | Fact supported | Status |", "| --- | --- | --- | --- |"])
    lines.extend(
        f"| {item.term} | {'yes' if item.in_resume else 'no'} | "
        f"{'yes' if item.fact_supported else 'no'} | {item.status} |"
        for item in review.coverage
    )
    if not review.coverage:
        lines.append("| None | no | no | no comparable terms |")
    lines.extend(["", "## 6. 修改优先级", ""])
    supported_missing = [item.term for item in review.coverage if item.status == "supported_but_missing"]
    priorities = list(review.rewrite_priorities)
    if supported_missing:
        priorities.append(
            "核对是否需要更显式地呈现这些已有事实支持的 JD 术语（精确短语缺失不等同于能力缺失）："
            + "、".join(supported_missing)
        )
    lines.extend(f"- {item}" for item in priorities or ["当前确定性检查没有发现必须修改项。"])
    lines.append("")
    return "\n".join(lines)


def render_aeo_html(review: AEOReview) -> str:
    markdown = render_aeo_markdown(review)
    escaped = html.escape(markdown)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AEO Resume Review</title><style>
body{{margin:0;background:#f6f8fb;color:#172033;font-family:-apple-system,"PingFang SC",sans-serif;line-height:1.65}}
main{{max-width:960px;margin:0 auto;padding:28px 18px 60px}}pre{{white-space:pre-wrap;background:#fff;border:1px solid #dce3ee;border-radius:12px;padding:22px;box-shadow:0 8px 28px #1d35571a}}
</style></head><body><main><pre>{escaped}</pre></main></body></html>"""


def write_aeo_review(review: AEOReview, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "aeo_review.md"
    html_path = output_dir / "aeo_review.html"
    md_path.write_text(render_aeo_markdown(review), encoding="utf-8")
    html_path.write_text(render_aeo_html(review), encoding="utf-8")
    return md_path, html_path

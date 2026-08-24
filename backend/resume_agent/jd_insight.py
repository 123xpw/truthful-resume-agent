"""explain-jd: a JD-understanding + fact-gap report, not a resume generator.

Per docs/project_reset.md, content here is split into two tiers on purpose:

- Tier A: structural extraction from the JD's own headings, plus the
  existing fact-bank matcher (`analyze_jd`) and not-writable blocklist. No
  LLM call, nothing here makes a claim beyond what the JD text and the
  fact bank already say.
- Tier B: role summary, capability map, interview follow-ups, and an
  explicitly experimental phrasing section call the configured LLM. The
  first two operate only on public JD text. Candidate-fact-touching output
  is advisory and has no path into resume generation. A cited fact_id plus
  a second model judgment reduces obvious errors but cannot prove that every
  generated detail is supported.
  If DeepSeek isn't reachable (no key, network error), each section
  degrades to an explicit error note — never a silent guess.

Computation happens once, in `build_jd_insight_data`. `render_markdown`
and `render_html` are pure renderers over that same data, so generating
both formats never calls the LLM twice.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import AnalysisResult, FactMatch, contains_keyword, extract_requirement_lines, is_low_signal_requirement_line
from .llm_client import LLMNotConfigured, chat_completion
from .rules import Fact

HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$", re.MULTILINE)

HARD_REQUIREMENT_HEADING = re.compile(r"职位要求|岗位要求|任职要求|能力要求")
BONUS_HEADING = re.compile(r"加分项|优先考虑|优先|Bonus")
RESPONSIBILITY_HEADING = re.compile(r"职位描述|岗位职责|工作职责|职责")


def _sections_by_heading(jd_text: str) -> dict[str, str]:
    """Split JD text into {heading: body} using its own '#'-style headings.

    Best-effort and structural only: a JD with no markdown headings (raw
    pasted text) yields an empty dict, which callers must handle by saying
    "not determinable from structure" rather than guessing.
    """
    matches = list(HEADING_RE.finditer(jd_text))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(jd_text)
        sections[match.group(1)] = jd_text[start:end]
    return sections


def _lines_for(sections: dict[str, str], pattern: re.Pattern) -> list[str]:
    lines: list[str] = []
    for heading, body in sections.items():
        if pattern.search(heading):
            lines.extend(
                line for line in extract_requirement_lines(body) if not is_low_signal_requirement_line(line)
            )
    return lines


def _classify_not_writable(
    not_writable: dict[str, str], hard_requirements: list[str], bonus_points: list[str]
) -> dict[str, str]:
    """For each not-writable tech, say whether it showed up under a hard-requirement
    heading, a bonus-point heading, both, or neither (structure couldn't tell).

    This exists because "硬要求" and "加分项" carry very different urgency —
    collapsing them into one hedge ("if this is a hard requirement or bonus
    point...") was the exact vagueness that made section 7 hard to act on.
    """
    alternative_markers = ("至少一门", "至少一种", "任意一门", "任意一种", "任一", "one of")

    def is_optional_alternative(line: str, tech: str) -> bool:
        if not contains_keyword(line, tech):
            return False
        lowered = line.lower()
        if any(marker in lowered for marker in alternative_markers):
            return True
        return bool(
            re.search(rf"{re.escape(tech)}[^。；;]*优先", line, flags=re.IGNORECASE)
            or re.search(rf"优先[^。；;]*{re.escape(tech)}", line, flags=re.IGNORECASE)
        )

    classification: dict[str, str] = {}
    for tech in not_writable:
        in_hard = any(
            contains_keyword(line, tech) and not is_optional_alternative(line, tech)
            for line in hard_requirements
        )
        in_bonus = any(contains_keyword(line, tech) for line in bonus_points) or any(
            is_optional_alternative(line, tech) for line in hard_requirements
        )
        if in_hard and in_bonus:
            classification[tech] = "both"
        elif in_hard:
            classification[tech] = "hard"
        elif in_bonus:
            classification[tech] = "bonus"
        else:
            classification[tech] = "unknown"
    return classification


def _prep_note(tech: str, tier: str) -> str:
    if tier == "hard":
        return f"『{tech}』出现在硬性要求里——没有真实项目支撑，这条硬性要求基本满足不了，需要优先补一个真实项目，不是靠措辞能绕过去的。"
    if tier == "bonus":
        return f"『{tech}』只出现在加分项里——是锦上添花，不补不影响硬性门槛，视你自己的时间精力决定要不要投入。"
    if tier == "both":
        return f"『{tech}』既在硬性要求也在加分项里出现——按硬性要求处理，优先级最高，需要真实项目。"
    return f"『{tech}』在 JD 里出现，但没能从标题结构判断它属于硬性要求还是加分项，建议自己对照 JD 原文判断优先级。"


def _llm_call_safe(messages: list[dict]) -> tuple[str | None, str | None]:
    """Return (content, error). Never raises — callers must handle both."""
    try:
        return chat_completion(messages), None
    except LLMNotConfigured as exc:
        return None, str(exc)
    except Exception as exc:  # network/HTTP errors from requests
        return None, f"{type(exc).__name__}: {exc}"


def llm_role_summary(jd_text: str) -> tuple[str | None, str | None]:
    prompt = (
        "阅读下面这份职位描述（JD），用中文回答：这个岗位实际上在招什么样的人？"
        "不要复述 JD 原文，用你自己的话总结这个岗位的核心画像，3-5 句话，"
        "不要提到任何具体候选人，只分析岗位本身。\n\n"
        f"JD 原文：\n{jd_text}"
    )
    return _llm_call_safe([{"role": "user", "content": prompt}])


def llm_capability_map(jd_text: str) -> tuple[str | None, str | None]:
    prompt = (
        "阅读下面这份职位描述（JD），输出一份'核心能力地图'：把 JD 里分散的要求，"
        "归纳成 4-8 个具体的能力项（不是照抄 JD 原句，是你归纳出的能力类别），"
        "每项一行，格式：能力名称 —— 一句话说明为什么 JD 需要这个能力。"
        "不要提到任何具体候选人，只分析岗位本身。\n\n"
        f"JD 原文：\n{jd_text}"
    )
    return _llm_call_safe([{"role": "user", "content": prompt}])


def llm_interview_followups(jd_text: str, matches: list[FactMatch]) -> tuple[str | None, str | None]:
    if not matches:
        return None, "没有匹配到事实，跳过面试追问。"
    fact_items = [
        {
            "fact_id": match.fact.id,
            "title": match.fact.title,
            "summary": match.fact.summary,
            "boundaries": list(match.fact.boundaries),
        }
        for match in matches
    ]
    prompt = (
        "你是一位面试官。根据 JD 和下面已经登记的经历，为每条经历设计 1-2 个追问。"
        "问题只能追问 summary 已出现的工作、boundaries 限定的范围或真实设计取舍；"
        "不能在问题里预设候选人使用过未出现的技术、指标、规模、架构或故障方案。"
        "只输出问题，不要输出答案、理想回答、考察点、评分标准或举例。"
        "严格输出 JSON 数组，格式："
        '[{"fact_id":"...","questions":["问题1","问题2"]}]。\n\n'
        f"JD：\n{jd_text}\n\n"
        f"经历：\n{json.dumps(fact_items, ensure_ascii=False)}"
    )
    content, error = _llm_call_safe([{"role": "user", "content": prompt}])
    if error or content is None:
        return None, error

    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None, "LLM 面试追问不是合法 JSON，已整体丢弃。"

    known = {match.fact.id: match.fact for match in matches}
    lines: list[str] = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict) or item.get("fact_id") not in known:
            continue
        questions = item.get("questions")
        if not isinstance(questions, list):
            continue
        accepted = [str(question).strip() for question in questions if isinstance(question, str) and question.strip()][:2]
        if not accepted:
            continue
        fact = known[item["fact_id"]]
        lines.append(f"### {fact.title} (`{fact.id}`)")
        lines.extend(f"- {question}" for question in accepted)
        lines.append("")
    if not lines:
        return None, "LLM 面试追问没有可追溯到已匹配 fact_id 的问题，已整体丢弃。"
    return "\n".join(lines).rstrip(), None


def _violates_boundaries(fact: Fact, phrasing: str) -> tuple[bool, str | None]:
    """Screen an advisory phrasing candidate against explicit boundaries.

    This is a second model judgment, not proof that the sentence is entailed
    by the fact. It may reject risky output, but its result must never be used
    to authorize resume generation.
    """
    prompt = (
        "下面是一条真实经历的边界限制（boundaries）和一句候选表达（phrasing）。"
        "只判断这句表达有没有违反、超出，或者暗示边界里明确排除/否定的内容。"
        "不要评价表达好不好，只判断是否越界。\n\n"
        f"边界：{'; '.join(fact.boundaries) or '（无特别边界）'}\n"
        f"表达：{phrasing}\n\n"
        "只回答一个词：OK（没有发现明确越界）或 VIOLATION（发现越界）。"
    )
    content, error = _llm_call_safe([{"role": "user", "content": prompt}])
    if error or content is None:
        return True, f"边界风险筛查调用失败，按越界处理：{error}"
    verdict = content.strip().upper()
    if verdict.startswith("OK"):
        return False, None
    return True, f"边界风险筛查发现越界：{content.strip()}"


def llm_phrasing_candidates(matches: list[FactMatch]) -> tuple[list[dict], list[str]]:
    """Return advisory wording candidates, never resume-authorized content.

    A known fact_id and boundary screen reduce obvious errors. They do not
    prove that every generated detail follows from the cited fact; the resume
    generator deliberately has no path that consumes this output.
    """
    if not matches:
        return [], ["没有已匹配的事实，跳过实验性表达建议。"]

    known_ids = {match.fact.id for match in matches}
    fact_block = "\n\n".join(
        f"fact_id: {match.fact.id}\n标题: {match.fact.title}\n摘要: {match.fact.summary}\n边界: {'; '.join(match.fact.boundaries)}"
        for match in matches
    )
    prompt = (
        "下面是已经登记的经历记录。为每条给出 1 条更清晰的候选表达。"
        "只能使用摘要和边界里出现的信息，不得添加工具、指标、成果或职责。"
        "严格输出 JSON 数组，不要输出 JSON 之外的文字，格式：\n"
        '[{"fact_id": "...", "phrasing": "..."}]\n\n'
        f"{fact_block}"
    )
    content, error = _llm_call_safe([{"role": "user", "content": prompt}])
    if error or content is None:
        return [], [f"LLM 调用失败：{error}"]

    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return [], ["LLM 输出不是合法 JSON，已整体丢弃。"]

    facts = {match.fact.id: match.fact for match in matches}
    accepted: list[dict] = []
    rejected: list[str] = []
    for item in parsed if isinstance(parsed, list) else []:
        fact_id = item.get("fact_id") if isinstance(item, dict) else None
        phrasing = item.get("phrasing") if isinstance(item, dict) else None
        if fact_id not in known_ids or not isinstance(phrasing, str) or not phrasing.strip():
            rejected.append(f"丢弃一条无法追溯到已匹配 fact_id 的建议：{item!r}")
            continue
        violated, reason = _violates_boundaries(facts[fact_id], phrasing)
        if violated:
            rejected.append(f"丢弃一条未通过边界风险筛查的建议（fact_id={fact_id}）：{reason}")
            continue
        accepted.append({"fact_id": fact_id, "phrasing": phrasing.strip()})
    return accepted, rejected


@dataclass
class JDInsightData:
    jd_path: Path
    jd_text: str
    job_type: str
    llm_enabled: bool
    role_summary: str | None
    role_summary_error: str | None
    capability_map: str | None
    capability_map_error: str | None
    responsibilities: list[str]
    hard_requirements: list[str]
    bonus_points: list[str]
    structural_split_found: bool
    strong_matches: list[FactMatch]
    weak_matches: list[FactMatch]
    not_writable: dict[str, str]
    not_writable_tiers: dict[str, str]
    interview_followups: str | None
    interview_followups_error: str | None
    phrasing_accepted: list[dict] = field(default_factory=list)
    phrasing_rejected: list[str] = field(default_factory=list)


def build_jd_insight_data(jd_path: Path, jd_text: str, result: AnalysisResult, use_llm: bool = True) -> JDInsightData:
    sections = _sections_by_heading(jd_text)
    responsibilities = _lines_for(sections, RESPONSIBILITY_HEADING)
    hard_requirements = _lines_for(sections, HARD_REQUIREMENT_HEADING)
    bonus_points = _lines_for(sections, BONUS_HEADING)
    matches = list(result.strong_matches) + list(result.weak_matches)

    role_summary = role_summary_error = None
    capability_map = capability_map_error = None
    interview_followups = interview_followups_error = None
    phrasing_accepted: list[dict] = []
    phrasing_rejected: list[str] = []
    if use_llm:
        role_summary, role_summary_error = llm_role_summary(jd_text)
        capability_map, capability_map_error = llm_capability_map(jd_text)
        interview_followups, interview_followups_error = llm_interview_followups(jd_text, matches)
        phrasing_accepted, phrasing_rejected = llm_phrasing_candidates(matches)

    return JDInsightData(
        jd_path=jd_path,
        jd_text=jd_text,
        job_type=result.job_type,
        llm_enabled=use_llm,
        role_summary=role_summary,
        role_summary_error=role_summary_error,
        capability_map=capability_map,
        capability_map_error=capability_map_error,
        responsibilities=responsibilities,
        hard_requirements=hard_requirements,
        bonus_points=bonus_points,
        structural_split_found=bool(responsibilities or hard_requirements or bonus_points),
        strong_matches=result.strong_matches,
        weak_matches=result.weak_matches,
        not_writable=result.not_writable,
        not_writable_tiers=_classify_not_writable(result.not_writable, hard_requirements, bonus_points),
        interview_followups=interview_followups,
        interview_followups_error=interview_followups_error,
        phrasing_accepted=phrasing_accepted,
        phrasing_rejected=phrasing_rejected,
    )


def render_gap_warning(data: JDInsightData) -> str:
    """Concise '缺什么预警' view: what the fact bank lacks and which matched
    facts are high-risk for follow-up questions."""
    lines = ["# 缺什么预警（面试备战）", ""]
    lines.append(f"- JD: `{data.jd_path}`")
    lines.append(f"- 岗位类型: **{data.job_type}**")
    lines.append("")

    lines.append("## 📌 缺证据（JD 要但你事实库没有 → 别写，或补项目）")
    lines.append("")
    if data.not_writable:
        for tech, reason in data.not_writable.items():
            tier = data.not_writable_tiers.get(tech, "")
            tier_label = f"（{tier}）" if tier else ""
            lines.append(f"- **{tech}**{tier_label}: {reason}")
    else:
        lines.append("- 无")
    lines.append("")

    matches = [*data.strong_matches, *data.weak_matches]
    high_risk = [m for m in matches if m.fact.risk == "high"]
    low_risk = [m for m in matches if m.fact.risk == "low"]
    mid_risk = [m for m in matches if m.fact.risk not in {"high", "low"}]

    lines.append("## ⚠️ 会被深挖（有事实但风险高 → 准备拷打问题）")
    lines.append("")
    if high_risk:
        for m in high_risk:
            lines.append(f"- {m.fact.title}（`{m.fact.id}`）")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 🔶 中风险（能答但要注意边界）")
    lines.append("")
    if mid_risk:
        for m in mid_risk:
            lines.append(f"- {m.fact.title}（`{m.fact.id}`）")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## ✅ 稳的（能答清）")
    lines.append("")
    if low_risk:
        for m in low_risk:
            lines.append(f"- {m.fact.title}（`{m.fact.id}`）")
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def render_gap_warning_html(data: JDInsightData) -> str:
    """Self-contained HTML dashboard for the concise gap warning."""
    matches = [*data.strong_matches, *data.weak_matches]
    high = [m for m in matches if m.fact.risk == "high"]
    mid = [m for m in matches if m.fact.risk not in {"high", "low"}]
    low = [m for m in matches if m.fact.risk == "low"]

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def fact_items(items) -> str:
        if not items:
            return '<li class="empty">无</li>'
        return "".join(
            f"<li><b>{esc(m.fact.title)}</b><code>{esc(m.fact.id)}</code></li>" for m in items
        )

    nw_items = "".join(
        f'<li><b>{esc(tech)}</b><span class="tag">{esc(data.not_writable_tiers.get(tech, ""))}</span>'
        f'<p>{esc(reason)}</p></li>'
        for tech, reason in data.not_writable.items()
    ) or '<li class="empty">无</li>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>缺什么预警 · {esc(data.job_type)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #f6f8fa; color: #1f2328; line-height: 1.6; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 28px 18px 60px; }}
  header {{ background: #2f5597; color: #fff; padding: 22px 26px; border-radius: 12px; margin-bottom: 18px; }}
  header h1 {{ margin: 0 0 6px; font-size: 22px; }}
  header p {{ margin: 0; opacity: .9; font-size: 14px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 640px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  .card {{ background: #fff; border: 1px solid #d8dee4; border-radius: 10px; padding: 16px 18px; }}
  .card h2 {{ margin: 0 0 10px; font-size: 16px; display: flex; align-items: center; gap: 6px; }}
  .card.red {{ border-top: 4px solid #d1242f; }}
  .card.yellow {{ border-top: 4px solid #bf8700; }}
  .card.orange {{ border-top: 4px solid #bc4c00; }}
  .card.green {{ border-top: 4px solid #1a7f37; }}
  ul {{ margin: 0; padding-left: 20px; }}
  li {{ margin: 6px 0; font-size: 14px; }}
  li.empty {{ color: #8b949e; list-style: none; margin-left: -20px; }}
  code {{ font-family: ui-monospace, Menlo, monospace; font-size: 12px; background: #f0f3f6;
         padding: 1px 6px; border-radius: 4px; margin-left: 6px; color: #57606a; }}
  .tag {{ display: inline-block; font-size: 11px; font-weight: 600; padding: 0 7px;
         border-radius: 9px; margin-left: 6px; background: #eaeef2; color: #57606a; }}
  .card.red .tag {{ background: #ffebe9; color: #d1242f; }}
  .card.yellow .tag {{ background: #fff8e1; color: #9a6700; }}
  p {{ margin: 2px 0 0; font-size: 13px; color: #57606a; }}
  footer {{ margin-top: 18px; background: #fff; border: 1px solid #d8dee4; border-radius: 10px;
           padding: 14px 18px; font-size: 13px; color: #57606a; }}
  footer a {{ color: #2f5597; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>缺什么预警（面试备战）</h1>
  <p>岗位类型：<b>{esc(data.job_type)}</b> · JD: <code>{esc(str(data.jd_path))}</code></p>
</header>

<div class="grid">
  <div class="card red">
    <h2>🔴 缺证据（别写，或补项目）</h2>
    <ul>{nw_items}</ul>
  </div>
  <div class="card yellow">
    <h2>🟡 会被深挖（准备拷打问题）</h2>
    <ul>{fact_items(high)}</ul>
  </div>
  <div class="card orange">
    <h2>🟠 中风险（注意边界）</h2>
    <ul>{fact_items(mid)}</ul>
  </div>
  <div class="card green">
    <h2>🟢 稳的（能答清）</h2>
    <ul>{fact_items(low)}</ul>
  </div>
</div>

<footer>
  拷打问题见 <code>interview_grill.md</code>（项目根目录）。本页为本地私有视图，数据来源于事实库事实与 JD 匹配结果。
</footer>
</div>
</body>
</html>"""


def render_markdown(data: JDInsightData) -> str:
    lines: list[str] = [
        "# JD Insight Report",
        "",
        f"- JD source: `{data.jd_path}`",
        f"- Inferred job type: **{data.job_type}**",
        "",
        "This report has two kinds of content: sections computed directly from "
        "the JD text and the fact bank (checkable, no LLM involved), and "
        "sections generated by an LLM from JD text, plus interview questions "
        "tied to already-matched facts (never resume wording — see "
        "`docs/project_reset.md` for the guardrail split).",
        "",
        "## 0. JD 原文",
        "",
        "```text",
        data.jd_text.strip(),
        "```",
        "",
        "## 1. 这个岗位到底在招什么人  _(LLM)_",
        "",
    ]
    if not data.llm_enabled:
        lines.append("_本次使用 `--no-llm`，仅展示确定性分析。_ ")
    else:
        lines.append(data.role_summary if data.role_summary else f"_LLM 调用失败：{data.role_summary_error}_")
    lines.extend(["", "## 2. 核心能力地图  _(LLM)_", ""])
    if not data.llm_enabled:
        lines.append("_本次使用 `--no-llm`，未生成能力地图。_ ")
    else:
        lines.append(data.capability_map if data.capability_map else f"_LLM 调用失败：{data.capability_map_error}_")
    lines.extend(["", "## 3. 硬要求 / 隐性要求 / 加分项  _(规则提取)_", ""])

    if data.structural_split_found:
        lines.append("从 JD 自身的标题结构里切出来的，是文本切分，不是理解：")
        lines.append("")
        if data.responsibilities:
            lines.append("**职责 / 描述类条目:**")
            lines.extend(f"- {line}" for line in data.responsibilities)
            lines.append("")
        if data.hard_requirements:
            lines.append("**硬要求类条目:**")
            lines.extend(f"- {line}" for line in data.hard_requirements)
            lines.append("")
        if data.bonus_points:
            lines.append("**加分项类条目:**")
            lines.extend(f"- {line}" for line in data.bonus_points)
            lines.append("")
        lines.append("隐性要求：见第 2 节「核心能力地图」——那部分本身就是从 JD 里推断出的隐性能力项，不在这里重复。")
    else:
        lines.append("这份 JD 没有识别出标准的职位要求/加分项标题结构，无法做结构化拆分。")
    lines.append("")

    lines.append("## 4. 和事实库的匹配点  _(规则匹配)_")
    lines.append("")
    if data.strong_matches:
        lines.append("**Strong matches:**")
        for match in data.strong_matches:
            lines.append(f"- `{match.fact.id}` ({match.fact.title}) — matched: {', '.join(match.matched_keywords)}")
        lines.append("")
    if data.weak_matches:
        lines.append("**Weak matches:**")
        for match in data.weak_matches:
            lines.append(f"- `{match.fact.id}` ({match.fact.title}) — matched: {', '.join(match.matched_keywords)}")
        lines.append("")
    if not data.strong_matches and not data.weak_matches:
        lines.append("没有匹配到任何事实库条目。")
        lines.append("")

    lines.append("## 5. 不可写项  _(规则黑名单)_")
    lines.append("")
    if data.not_writable:
        for tech, reason in data.not_writable.items():
            lines.append(f"- **{tech}**: {reason}")
    else:
        lines.append("这份 JD 没有出现事实库无证据支持的关键词。")
    lines.append("")

    lines.extend(["", "## 6. 可能面试追问  _(LLM)_", ""])
    if not data.llm_enabled:
        lines.append("_本次使用 `--no-llm`，未生成面试追问。_ ")
    else:
        lines.append(data.interview_followups if data.interview_followups else f"_LLM 调用失败：{data.interview_followups_error}_")
    lines.extend(["", "## 7. 你可以补准备的方向  _(规则推导)_", ""])
    if data.not_writable or data.weak_matches:
        lines.append("基于第 4/5 节的确定性结果，值得考虑的方向（不是 LLM 生成，只是把已知差距列出来）：")
        for tech in data.not_writable:
            lines.append(f"- {_prep_note(tech, data.not_writable_tiers.get(tech, 'unknown'))}")
        for match in data.weak_matches:
            lines.append(f"- `{match.fact.id}` 只是弱匹配（{', '.join(match.matched_keywords)}）；可以考虑是否有相关但未登记的经历。")
    else:
        lines.append("第 4/5 节没有发现明显差距。")
    lines.append("")

    lines.extend(["", "## 8. 实验性表达候选  _(LLM，仅供审阅，不进入简历)_", ""])
    lines.append("事实编号与边界筛查只能减少明显错误，不能证明候选句完全由事实推出；简历生成器不会读取本节。")
    lines.append("")
    if not data.llm_enabled:
        lines.append("_本次使用 `--no-llm`，未生成实验性表达候选。_ ")
    elif data.phrasing_accepted:
        for item in data.phrasing_accepted:
            lines.append(f"- `{item['fact_id']}`: {item['phrasing']}")
    if data.phrasing_rejected:
        lines.append("")
        lines.append("**已丢弃的候选：**")
        lines.extend(f"- {note}" for note in data.phrasing_rejected)
    if data.llm_enabled and not data.phrasing_accepted and not data.phrasing_rejected:
        lines.append("没有已匹配的事实可供实验性改写。")

    return "\n".join(lines)


def _esc(text: str) -> str:
    return html_lib.escape(text)


def _md_bold_to_html(text: str) -> str:
    """Very small **bold** -> <strong> and newline -> <br> converter for LLM prose."""
    escaped = _esc(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = escaped.replace("\n", "<br>")
    return escaped


def render_html(data: JDInsightData) -> str:
    def badge(kind: str) -> str:
        cls = "badge-llm" if kind == "llm" else "badge-rule"
        label = "LLM 生成" if kind == "llm" else "规则 / 确定性"
        return f'<span class="badge {cls}">{label}</span>'

    def section(number: str, title: str, kind: str, body_html: str) -> str:
        return (
            f'<section class="card">'
            f'<h2>{number}. {_esc(title)} {badge(kind)}</h2>'
            f'<div class="body">{body_html}</div>'
            f"</section>"
        )

    def llm_body(content: str | None, error: str | None, disabled_text: str) -> str:
        if not data.llm_enabled:
            return f'<p class="note">{_esc(disabled_text)}</p>'
        if content:
            return _md_bold_to_html(content)
        return f'<p class="error">LLM 调用失败：{_esc(error or "unknown error")}</p>'

    matches_html = ""
    if data.strong_matches:
        matches_html += "<h3>Strong matches</h3><ul>" + "".join(
            f"<li><code>{_esc(m.fact.id)}</code> ({_esc(m.fact.title)}) — matched: {_esc(', '.join(m.matched_keywords))}</li>"
            for m in data.strong_matches
        ) + "</ul>"
    if data.weak_matches:
        matches_html += "<h3>Weak matches</h3><ul>" + "".join(
            f"<li><code>{_esc(m.fact.id)}</code> ({_esc(m.fact.title)}) — matched: {_esc(', '.join(m.matched_keywords))}</li>"
            for m in data.weak_matches
        ) + "</ul>"
    if not matches_html:
        matches_html = "<p>没有匹配到任何事实库条目。</p>"

    not_writable_html = (
        "<ul>" + "".join(f"<li><strong>{_esc(t)}</strong>: {_esc(r)}</li>" for t, r in data.not_writable.items()) + "</ul>"
        if data.not_writable
        else "<p>这份 JD 没有出现事实库无证据支持的关键词。</p>"
    )

    def req_block(title: str, items: list[str]) -> str:
        if not items:
            return ""
        return f"<h3>{_esc(title)}</h3><ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>"

    if data.structural_split_found:
        req_html = (
            "<p class=\"note\">从 JD 自身的标题结构里切出来的，是文本切分，不是理解。</p>"
            + req_block("职责 / 描述类条目", data.responsibilities)
            + req_block("硬要求类条目", data.hard_requirements)
            + req_block("加分项类条目", data.bonus_points)
            + "<p class=\"note\">隐性要求：见第 2 节「核心能力地图」。</p>"
        )
    else:
        req_html = "<p>这份 JD 没有识别出标准的职位要求/加分项标题结构，无法做结构化拆分。</p>"

    tier_tag = {
        "hard": '<span class="tier tier-hard">硬性</span>',
        "bonus": '<span class="tier tier-bonus">加分</span>',
        "both": '<span class="tier tier-hard">硬性</span>',
        "unknown": '<span class="tier tier-unknown">未知</span>',
    }
    prep_items = [
        f"{tier_tag[data.not_writable_tiers.get(t, 'unknown')]} {_esc(_prep_note(t, data.not_writable_tiers.get(t, 'unknown')))}"
        for t in data.not_writable
    ] + [
        f"<code>{_esc(m.fact.id)}</code> 只是弱匹配（{_esc(', '.join(m.matched_keywords))}）；可以考虑是否有相关但未登记的经历。"
        for m in data.weak_matches
    ]
    prep_html = ("<ul>" + "".join(f"<li>{item}</li>" for item in prep_items) + "</ul>") if prep_items else "<p>第 4/5 节没有发现明显差距。</p>"

    phrasing_html = (
        '<p class="note">事实编号与边界筛查只能减少明显错误，不能证明候选句完全由事实推出；简历生成器不会读取本节。</p>'
    )
    if not data.llm_enabled:
        phrasing_html += '<p class="note">本次使用 --no-llm，未生成实验性表达候选。</p>'
    elif data.phrasing_accepted:
        phrasing_html += "<ul>" + "".join(
            f"<li><code>{_esc(item['fact_id'])}</code>: {_esc(item['phrasing'])}</li>"
            for item in data.phrasing_accepted
        ) + "</ul>"
    if data.phrasing_rejected:
        phrasing_html += '<h3>已丢弃的候选</h3><ul>' + "".join(
            f"<li>{_esc(note)}</li>" for note in data.phrasing_rejected
        ) + "</ul>"
    if data.llm_enabled and not data.phrasing_accepted and not data.phrasing_rejected:
        phrasing_html += "<p>没有已匹配的事实可供实验性改写。</p>"

    body_sections = [
        section("1", "和事实库的匹配点", "rule", matches_html),
        section("2", "不可写项", "rule", not_writable_html),
        section("3", "硬要求 / 隐性要求 / 加分项", "rule", req_html),
        section("4", "你可以补准备的方向", "rule", prep_html),
    ]
    if data.llm_enabled:
        body_sections.extend(
            [
                section(
                    "5",
                    "这个岗位到底在招什么人",
                    "llm",
                    llm_body(data.role_summary, data.role_summary_error, ""),
                ),
                section(
                    "6",
                    "核心能力地图",
                    "llm",
                    llm_body(data.capability_map, data.capability_map_error, ""),
                ),
                section(
                    "7",
                    "可能面试追问",
                    "llm",
                    llm_body(data.interview_followups, data.interview_followups_error, ""),
                ),
                section("8", "实验性表达候选（仅供审阅，不进入简历）", "llm", phrasing_html),
            ]
        )
    body_sections.append(
        '<details class="card raw-jd"><summary>JD 原文（点击展开） '
        + badge("rule")
        + f'</summary><pre class="jdtext">{_esc(data.jd_text.strip())}</pre></details>'
    )
    body = "".join(body_sections)
    disclaimer = (
        '灰色徽章表示代码直接计算的确定性内容；橙色徽章表示只供人工审阅的 LLM 输出。'
        'LLM 建议不能进入简历生成器。'
        if data.llm_enabled
        else '本报告使用 <code>--no-llm</code>：只展示可逐字核对的 JD 结构、事实匹配和不可写边界。'
    )

    return f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>JD Insight — {_esc(data.job_type)}</title>
<style>
  :root {{
    --bg: #f7f7f5; --card-bg: #ffffff; --text: #1f2328; --muted: #6b7280;
    --border: #e5e7eb; --rule: #eef2ff; --rule-text: #3730a3;
    --llm: #fff7ed; --llm-text: #9a3412; --code-bg: #f3f4f6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16181d; --card-bg: #1f2228; --text: #e6e6e6; --muted: #9ca3af;
      --border: #2d3038; --rule: #1e2340; --rule-text: #a5b4fc;
      --llm: #3a2412; --llm-text: #fdba74; --code-bg: #262a33;
    }}
  }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", sans-serif; margin: 0; padding: 2rem 1rem 4rem; line-height: 1.6; }}
  .wrap {{ max-width: 860px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
  .disclaimer {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 0.9rem 1.1rem; font-size: 0.9rem; color: var(--muted); margin-bottom: 1.5rem; }}
  .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.2rem 1.4rem; margin-bottom: 1rem; }}
  .card h2 {{ font-size: 1.05rem; margin: 0 0 0.7rem; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
  .card h3 {{ font-size: 0.95rem; margin: 0.9rem 0 0.4rem; }}
  details.card summary {{ cursor: pointer; font-weight: 650; }}
  details.card[open] summary {{ margin-bottom: 0.8rem; }}
  .badge {{ font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.55rem; border-radius: 999px; }}
  .badge-rule {{ background: var(--rule); color: var(--rule-text); }}
  .badge-llm {{ background: var(--llm); color: var(--llm-text); }}
  code {{ background: var(--code-bg); padding: 0.1rem 0.35rem; border-radius: 4px; font-size: 0.85em; }}
  .jdtext {{ white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 0.85rem; background: var(--code-bg); padding: 1rem; border-radius: 8px; max-height: 420px; overflow-y: auto; }}
  .note {{ color: var(--muted); font-size: 0.85rem; }}
  .error {{ color: #dc2626; }}
  .tier {{ display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 4px; margin-right: 0.3rem; }}
  .tier-hard {{ background: #fee2e2; color: #991b1b; }}
  .tier-bonus {{ background: #dcfce7; color: #166534; }}
  .tier-unknown {{ background: #f3f4f6; color: #6b7280; }}
  @media (prefers-color-scheme: dark) {{
    .tier-hard {{ background: #4c1d1d; color: #fca5a5; }}
    .tier-bonus {{ background: #14311f; color: #86efac; }}
    .tier-unknown {{ background: #2d3038; color: #9ca3af; }}
  }}
  ul {{ padding-left: 1.3rem; }}
  li {{ margin-bottom: 0.3rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>JD Insight Report</h1>
  <div class="meta">JD source: <code>{_esc(str(data.jd_path))}</code> &nbsp;·&nbsp; Inferred job type: <strong>{_esc(data.job_type)}</strong></div>
  <div class="disclaimer">{disclaimer}</div>
  {body}
</div>
</body>
</html>"""


def write_jd_insight(output_dir: Path, data: JDInsightData) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "jd_insight.md"
    html_path = output_dir / "jd_insight.html"
    md_path.write_text(render_markdown(data), encoding="utf-8")
    html_path.write_text(render_html(data), encoding="utf-8")
    return md_path, html_path

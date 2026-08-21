from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .analyzer import AnalysisResult, FactMatch
from .fragments import ResumeFragment
from .layout_config import SECTION_LIMITS
from .llm_client import LLMNotConfigured, chat_completion
from .rules import Fact


RISK_ORDER = {"low": 2, "medium": 1, "high": 0}


@dataclass(frozen=True)
class SelectionPlan:
    selected_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    signals: tuple[tuple[str, str], ...]
    source: str
    note: str


def _match_map(result: AnalysisResult) -> dict[str, FactMatch]:
    return {match.fact.id: match for match in [*result.strong_matches, *result.weak_matches]}


def _candidate_payload(
    fragment_id: str,
    level: str,
    fragment: ResumeFragment,
    facts: dict[str, Fact],
    matches: dict[str, FactMatch],
) -> dict:
    source_facts = [facts[fact_id] for fact_id in fragment.source_fact_ids if fact_id in facts]
    signals = [matches[fact_id] for fact_id in fragment.source_fact_ids if fact_id in matches]
    return {
        "fragment_id": fragment_id,
        "section": fragment.section,
        "title": fragment.title,
        "confirmed_wording": level,
        "match_signals": [
            {
                "fact_id": match.fact.id,
                "level": match.level,
                "matched_keywords": match.matched_keywords,
            }
            for match in signals
        ],
        "facts": [
            {
                "fact_id": fact.id,
                "summary": fact.summary,
                "boundaries": list(fact.boundaries),
                "risk": fact.risk,
            }
            for fact in source_facts
        ],
    }


def _fallback_score(
    fragment_id: str,
    level: str,
    fragment: ResumeFragment,
    facts: dict[str, Fact],
    matches: dict[str, FactMatch],
    order_index: int,
) -> tuple[int, int, int, int, int]:
    source_matches = [matches[fact_id] for fact_id in fragment.source_fact_ids if fact_id in matches]
    strongest = max((2 if match.level == "strong" else 1 for match in source_matches), default=0)
    keyword_count = sum(len(match.matched_keywords) for match in source_matches)
    source_risks = [RISK_ORDER.get(facts[fact_id].risk, 0) for fact_id in fragment.source_fact_ids if fact_id in facts]
    risk_score = min(source_risks, default=0)
    wording_score = 1 if level == "A" else 0
    return strongest, keyword_count, wording_score, risk_score, -order_index


def _candidate_signal(
    fragment_id: str,
    level: str,
    fragment: ResumeFragment,
    facts: dict[str, Fact],
    matches: dict[str, FactMatch],
) -> str:
    source_matches = [matches[fact_id] for fact_id in fragment.source_fact_ids if fact_id in matches]
    if source_matches:
        match_text = "; ".join(
            f"{match.level} via {', '.join(match.matched_keywords)}" for match in source_matches
        )
    else:
        match_text = "no direct matcher signal"
    risks = sorted(
        {facts[fact_id].risk for fact_id in fragment.source_fact_ids if fact_id in facts}
    )
    risk_text = ", ".join(risks) if risks else "unknown"
    return f"confirmed wording={level}; JD match={match_text}; source risk={risk_text}"


def deterministic_selection(
    ordered_ids: list[str],
    selected_levels: dict[str, str],
    fragments: dict[str, ResumeFragment],
    facts: dict[str, Fact],
    result: AnalysisResult,
) -> SelectionPlan:
    matches = _match_map(result)
    selected: list[str] = []
    for section, limit in SECTION_LIMITS.items():
        candidates = [fragment_id for fragment_id in ordered_ids if fragments[fragment_id].section == section]
        ranked = sorted(
            enumerate(candidates),
            key=lambda item: _fallback_score(
                item[1],
                selected_levels[item[1]],
                fragments[item[1]],
                facts,
                matches,
                item[0],
            ),
            reverse=True,
        )
        chosen = {fragment_id for _, fragment_id in ranked[:limit]}
        selected.extend(fragment_id for fragment_id in candidates if fragment_id in chosen)
    omitted = [fragment_id for fragment_id in ordered_ids if fragment_id not in selected]
    signals = tuple(
        (
            fragment_id,
            _candidate_signal(
                fragment_id,
                selected_levels[fragment_id],
                fragments[fragment_id],
                facts,
                matches,
            ),
        )
        for fragment_id in ordered_ids
    )
    return SelectionPlan(
        selected_ids=tuple(selected),
        omitted_ids=tuple(omitted),
        signals=signals,
        source="deterministic_fallback",
        note="Selected by visible match strength, wording readiness, risk, and stable order; no LLM ranking was used.",
    )


def _parse_selected_ids(content: str) -> list[str]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("selected_fragment_ids"), list):
        raise ValueError("selection response must contain selected_fragment_ids")
    ids = parsed["selected_fragment_ids"]
    if not all(isinstance(fragment_id, str) for fragment_id in ids):
        raise ValueError("selected_fragment_ids must contain only strings")
    if len(ids) != len(set(ids)):
        raise ValueError("selected_fragment_ids contains duplicates")
    return ids


def llm_selection(
    jd_text: str,
    ordered_ids: list[str],
    selected_levels: dict[str, str],
    fragments: dict[str, ResumeFragment],
    facts: dict[str, Fact],
    result: AnalysisResult,
) -> SelectionPlan:
    matches = _match_map(result)
    candidates = [
        _candidate_payload(
            fragment_id,
            selected_levels[fragment_id],
            fragments[fragment_id],
            facts,
            matches,
        )
        for fragment_id in ordered_ids
    ]
    prompt = (
        "你是大厂技术岗位简历编辑。请从候选列表中为这一份 JD 选择最有利于获得面试、"
        "同时能够被事实支撑的经历。你只能返回候选列表中已有的 fragment_id，不能新增、改写或猜测经历。"
        "优先考虑：对 JD 核心职责的直接覆盖、工程深度、可展示产物、差异化和面试可解释性；"
        "降低只有浅层关键词命中、依赖外部开发支持或边界风险较高经历的优先级。"
        "实习经历最多选择 3 项，项目经历最多选择 3 项；某栏目候选不足时全部选择。"
        "严格输出 JSON 对象，且只包含 selected_fragment_ids 字段，例如："
        '{"selected_fragment_ids":["id1","id2"]}。\n\n'
        f"JD：\n{jd_text}\n\n候选：\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    content = chat_completion([{"role": "user", "content": prompt}], temperature=0.0)
    selected_ids = _parse_selected_ids(content)
    known = set(ordered_ids)
    unknown = sorted(set(selected_ids) - known)
    if unknown:
        raise ValueError(f"selection response contains unknown fragment IDs: {', '.join(unknown)}")
    for section, limit in SECTION_LIMITS.items():
        section_candidates = [fragment_id for fragment_id in ordered_ids if fragments[fragment_id].section == section]
        section_selected = [fragment_id for fragment_id in selected_ids if fragments[fragment_id].section == section]
        expected = min(limit, len(section_candidates))
        if len(section_selected) != expected:
            raise ValueError(
                f"selection response chose {len(section_selected)} {section} entries; expected {expected}"
            )
    selected = [fragment_id for fragment_id in ordered_ids if fragment_id in set(selected_ids)]
    omitted = [fragment_id for fragment_id in ordered_ids if fragment_id not in set(selected_ids)]
    signals = tuple(
        (
            fragment_id,
            _candidate_signal(
                fragment_id,
                selected_levels[fragment_id],
                fragments[fragment_id],
                facts,
                matches,
            ),
        )
        for fragment_id in ordered_ids
    )
    return SelectionPlan(
        selected_ids=tuple(selected),
        omitted_ids=tuple(omitted),
        signals=signals,
        source="llm_restricted_ids",
        note="The LLM ranked only existing confirmed fragment IDs; code validated identity, section, and capacity.",
    )


def build_selection_plan(
    jd_text: str,
    ordered_ids: list[str],
    selected_levels: dict[str, str],
    fragments: dict[str, ResumeFragment],
    facts: dict[str, Fact],
    result: AnalysisResult,
    use_llm: bool = False,
) -> SelectionPlan:
    if use_llm:
        try:
            return llm_selection(jd_text, ordered_ids, selected_levels, fragments, facts, result)
        except LLMNotConfigured as exc:
            raise ValueError(f"LLM selection requested but not configured: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"LLM selection failed closed: {type(exc).__name__}: {exc}") from exc
    return deterministic_selection(ordered_ids, selected_levels, fragments, facts, result)


def render_selection_report(plan: SelectionPlan, fragments: dict[str, ResumeFragment]) -> str:
    signals = dict(plan.signals)
    lines = [
        "# Resume Selection Plan",
        "",
        f"- source: `{plan.source}`",
        f"- note: {plan.note}",
        "- selection is editorial; it does not change fact truth or candidate confirmation.",
        "",
        "## Included",
    ]
    lines.extend(
        f"- `{fragment_id}` — {fragments[fragment_id].title} ({fragments[fragment_id].section}); "
        f"{signals[fragment_id]}"
        for fragment_id in plan.selected_ids
    )
    lines.extend(["", "## Omitted For This Resume"])
    if plan.omitted_ids:
        lines.extend(
            f"- `{fragment_id}` — {fragments[fragment_id].title}: omitted after JD-fit and "
            f"page-capacity ranking; {signals[fragment_id]}"
            for fragment_id in plan.omitted_ids
        )
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def write_selection_report(plan: SelectionPlan, fragments: dict[str, ResumeFragment], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "selection_plan.md"
    path.write_text(render_selection_report(plan, fragments), encoding="utf-8")
    return path

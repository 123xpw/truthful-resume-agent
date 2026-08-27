"""Deterministic, evidence-grounded preview of one job description.

This module deliberately does not call an LLM and does not persist the JD.
It extracts explicit list items under JD headings, then evaluates each item
against one immutable fact-bank snapshot.  Every positive match cites a
``fact_id``; unsupported claims cite the deterministic guardrail that blocked
them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Iterable, Literal

from .analyzer import contains_keyword, infer_job_type
from .fact_store import load_facts
from .rules import Fact, find_not_writable, has_fact_evidence, term_matches


RequirementKind = Literal["responsibility", "hard_requirement", "bonus", "unclassified"]
EvidenceLevel = Literal["direct_support", "partial_support", "no_evidence", "not_writable"]

LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*•]|\d+[.、)]|[（(]\d+[）)])\s*(.+?)\s*$"
)
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")

HEADING_PATTERNS: tuple[tuple[RequirementKind, re.Pattern[str]], ...] = (
    ("bonus", re.compile(r"^(?:加分项|加分条件|优先考虑|优先条件|bonus(?:\s+points?)?)$", re.I)),
    (
        "responsibility",
        re.compile(r"^(?:职位描述|岗位描述|职位职责|岗位职责|工作职责|职责|responsibilit(?:y|ies))$", re.I),
    ),
    (
        "hard_requirement",
        re.compile(r"^(?:职位要求|岗位要求|任职要求|能力要求|基本要求|requirements?|qualifications?)$", re.I),
    ),
)

GROUP_LABEL_RE = re.compile(
    r"^(?:基础条件|专业能力|能力特质|技术能力|工程能力|岗位能力|任职资格|其他要求)\s*[:：]\s*$",
    re.I,
)

# These are claim-strength/operating-scale boundaries rather than technologies.
# They stay local to the preview so the legacy matcher contract is unchanged.
CLAIM_GUARDRAILS: dict[str, str] = {
    "生产级": "事实库没有证明相关代码或系统达到经过真实运行验证的生产级边界。",
    "production-grade": "The fact bank does not prove production-grade code or systems validated in real operation.",
    "多用户": "事实库没有证明多用户服务、身份隔离或权限治理。",
    "multi-user": "The fact bank does not prove multi-user serving, isolation, or access control.",
    "多租户": "事实库没有证明多租户隔离或租户级治理。",
    "multi-tenant": "The fact bank does not prove multi-tenant isolation or governance.",
    "SLA": "事实库没有证明可用性 SLA 或相应的生产监控与故障治理。",
}


@dataclass(frozen=True)
class ExtractedRequirement:
    requirement_id: str
    kind: RequirementKind
    jd_text: str


@dataclass(frozen=True)
class FactEvidence:
    fact_id: str
    title: str
    support: Literal["direct", "partial"]
    matched_keywords: tuple[str, ...]
    summary: str
    boundaries: tuple[str, ...]
    risk: str


@dataclass(frozen=True)
class BlockedClaim:
    term: str
    reason: str
    source: Literal["technology_guardrail", "claim_boundary_guardrail"]


@dataclass(frozen=True)
class RequirementEvidence:
    requirement_id: str
    kind: RequirementKind
    jd_text: str
    evidence_level: EvidenceLevel
    evidence: tuple[FactEvidence, ...]
    blocked_claims: tuple[BlockedClaim, ...]
    has_mixed_evidence: bool
    rationale: str


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(":：")).strip()


def _heading_kind(text: str) -> RequirementKind | None:
    normalized = _normalize_heading(text)
    for kind, pattern in HEADING_PATTERNS:
        if pattern.fullmatch(normalized):
            return kind
    return None


def extract_requirements(jd_text: str) -> tuple[list[ExtractedRequirement], list[dict[str, str]], str]:
    """Extract explicit list items without interpreting prose.

    Markdown headings and plain one-line headings are supported.  If no known
    heading is present, list items are retained as ``unclassified`` rather
    than guessed to be hard requirements.
    """

    current_kind: RequirementKind | None = None
    rows: list[tuple[RequirementKind, str]] = []
    recognized_markdown = False
    recognized_plain = False
    ignored_group_labels = 0

    for raw_line in jd_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        markdown_heading = MARKDOWN_HEADING_RE.match(stripped)
        if markdown_heading:
            heading_kind = _heading_kind(markdown_heading.group(1))
            current_kind = heading_kind
            recognized_markdown = recognized_markdown or heading_kind is not None
            continue

        # Plain headings are accepted only when the complete line is a known
        # heading.  This prevents prose containing words such as "要求" from
        # silently changing the section classification.
        plain_kind = _heading_kind(stripped)
        if plain_kind is not None:
            current_kind = plain_kind
            recognized_plain = True
            continue

        list_item = LIST_ITEM_RE.match(raw_line)
        if not list_item:
            continue
        text = list_item.group(1).strip()
        if not text:
            continue
        if GROUP_LABEL_RE.fullmatch(text):
            ignored_group_labels += 1
            continue
        rows.append((current_kind or "unclassified", text))

    warnings: list[dict[str, str]] = []
    if recognized_markdown:
        structure = "markdown_headings"
    elif recognized_plain:
        structure = "plain_headings"
    else:
        structure = "unclassified_list"
        if rows:
            warnings.append(
                {
                    "code": "JD_STRUCTURE_NOT_FOUND",
                    "message": "未识别到职责/要求/加分项标题；列表项已保留，但不会猜测其类别。",
                }
            )

    if ignored_group_labels:
        warnings.append(
            {
                "code": "GROUP_LABELS_IGNORED",
                "message": f"忽略了 {ignored_group_labels} 个只有分组名称、没有具体要求的列表项。",
            }
        )
    if not rows:
        warnings.append(
            {
                "code": "NO_EXPLICIT_REQUIREMENTS",
                "message": "没有提取到明确的项目符号或编号要求；系统未从连续正文中推断要求。",
            }
        )

    counters: dict[RequirementKind, int] = {
        "responsibility": 0,
        "hard_requirement": 0,
        "bonus": 0,
        "unclassified": 0,
    }
    extracted: list[ExtractedRequirement] = []
    for kind, text in rows:
        counters[kind] += 1
        extracted.append(
            ExtractedRequirement(
                requirement_id=f"{kind}-{counters[kind]}",
                kind=kind,
                jd_text=text,
            )
        )
    return extracted, warnings, structure


def _matched_keywords(text: str, fact: Fact) -> tuple[str, ...]:
    return tuple(dict.fromkeys(keyword for keyword in fact.keywords if contains_keyword(text, keyword)))


def _fact_evidence(text: str, facts: Iterable[Fact]) -> tuple[FactEvidence, ...]:
    matches: list[FactEvidence] = []
    for fact in facts:
        keywords = _matched_keywords(text, fact)
        if not keywords:
            continue
        # Two independent exact keyword hits are direct evidence.  A single
        # hit is also direct when the fact summary itself explicitly names
        # that term; otherwise it remains a weaker retrieval hint.
        direct = len(keywords) >= 2 or any(contains_keyword(fact.summary, keyword) for keyword in keywords)
        matches.append(
            FactEvidence(
                fact_id=fact.id,
                title=fact.title,
                support="direct" if direct else "partial",
                matched_keywords=keywords,
                summary=fact.summary,
                boundaries=fact.boundaries,
                risk=fact.risk,
            )
        )
    matches.sort(
        key=lambda item: (
            item.support == "direct",
            len(item.matched_keywords),
            item.fact_id,
        ),
        reverse=True,
    )
    return tuple(matches)


def _blocked_claims(text: str, facts: tuple[Fact, ...]) -> tuple[BlockedClaim, ...]:
    blocked = [
        BlockedClaim(term=term, reason=reason, source="technology_guardrail")
        for term, reason in find_not_writable(text, facts).items()
    ]
    for term, reason in CLAIM_GUARDRAILS.items():
        if term_matches(text, term) and not has_fact_evidence(term, facts):
            blocked.append(
                BlockedClaim(term=term, reason=reason, source="claim_boundary_guardrail")
            )
    blocked.sort(key=lambda item: (item.source, item.term.lower()))
    return tuple(blocked)


def evaluate_requirement(requirement: ExtractedRequirement, facts: Iterable[Fact]) -> RequirementEvidence:
    fact_snapshot = tuple(facts)
    evidence = _fact_evidence(requirement.jd_text, fact_snapshot)
    blocked = _blocked_claims(requirement.jd_text, fact_snapshot)
    direct = [item for item in evidence if item.support == "direct"]
    partial = [item for item in evidence if item.support == "partial"]

    if blocked:
        level: EvidenceLevel = "not_writable"
        if evidence:
            rationale = "部分内容命中事实，但要求中仍包含事实库未支持的明确技术或强度边界。"
        else:
            rationale = "要求包含事实库未支持的明确技术或强度边界，不得据此生成简历表述。"
    elif direct:
        level = "direct_support"
        rationale = "至少一条事实摘要明确支持命中词，或同一事实命中两个及以上明确关键词。"
    elif partial:
        level = "partial_support"
        rationale = "只命中事实关键词，事实摘要尚不足以证明完整要求，需要人工核对。"
    else:
        level = "no_evidence"
        rationale = "当前事实库没有找到与该条要求直接对应的事实或关键词。"

    return RequirementEvidence(
        requirement_id=requirement.requirement_id,
        kind=requirement.kind,
        jd_text=requirement.jd_text,
        evidence_level=level,
        evidence=evidence,
        blocked_claims=blocked,
        has_mixed_evidence=bool(blocked and evidence),
        rationale=rationale,
    )


def build_job_analysis_preview(jd_text: str, facts: Iterable[Fact] | None = None) -> dict:
    """Build a JSON-ready preview with no model calls and no persistence."""

    if not jd_text.strip():
        raise ValueError("JD text is empty")
    fact_snapshot = tuple(load_facts() if facts is None else facts)
    requirements, warnings, structure = extract_requirements(jd_text)
    evidence_rows = [evaluate_requirement(requirement, fact_snapshot) for requirement in requirements]

    by_kind = {kind: 0 for kind in ("responsibility", "hard_requirement", "bonus", "unclassified")}
    by_level = {level: 0 for level in ("direct_support", "partial_support", "no_evidence", "not_writable")}
    for row in evidence_rows:
        by_kind[row.kind] += 1
        by_level[row.evidence_level] += 1

    return {
        "job_type": infer_job_type(jd_text),
        "structure": structure,
        "interpretation": {
            "unit": "one explicit JD list item",
            "note": "证据等级只说明该条要求中存在可追溯的支持点，不等于候选人已经完整满足整条复合要求。",
            "direct_support": "事实摘要明确支持命中词，或同一事实命中两个及以上明确关键词。",
            "partial_support": "只在事实关键词中命中，仍需人工核对。",
            "no_evidence": "当前事实库没有找到对应证据。",
            "not_writable": "包含明确但无事实支持的技术或强度边界；即使同一行有其他证据，也按失败关闭。",
        },
        "requirements": [asdict(row) for row in evidence_rows],
        "summary": {
            "total_requirements": len(evidence_rows),
            "by_kind": by_kind,
            "by_evidence_level": by_level,
        },
        "warnings": warnings,
        "saved": False,
        "llm_calls": 0,
    }

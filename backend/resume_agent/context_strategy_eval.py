"""Compare full fact context with the Agent's keyword top-k context.

This is a small, private decision experiment rather than a public benchmark.
It sends the selected JD and controlled fact summaries to the configured LLM.
Reports should stay under ignored ``data/evaluation/context_strategy_*`` paths.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable, Iterable

from .agent.tools import build_search_payload
from .eval_matchers import load_audit_labels
from .fact_store import load_facts
from .llm_client import chat_completion
from .rules import Fact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTS = PROJECT_ROOT / "data" / "facts" / "facts.json"
DEFAULT_LABELS = PROJECT_ROOT / "data" / "evaluation" / "target_matcher_labels.v2.json"
DEFAULT_REPORT = PROJECT_ROOT / "data" / "evaluation" / "context_strategy_report.v1.json"
MODES = ("full_context", "keyword_top5")


@dataclass(frozen=True)
class Selection:
    fact_id: str
    relevance: str
    reason: str
    safe_claim: str


@dataclass(frozen=True)
class RunResult:
    mode: str
    status: str
    selected: tuple[Selection, ...]
    unknown_fact_ids: tuple[str, ...]
    out_of_context_fact_ids: tuple[str, ...]
    useful_selected: int
    marginal_selected: int
    irrelevant_selected: int
    useful_total: int
    useful_recall: float
    useful_precision: float
    supported_precision: float
    evidence_fact_count: int
    prompt_chars: int
    response_chars: int
    latency_ms: int
    error: str = ""


def _fact_payload(fact: Fact) -> dict:
    return {
        "fact_id": fact.id,
        "title": fact.title,
        "summary": fact.summary,
        "boundaries": list(fact.boundaries),
        "risk": fact.risk,
    }


def evidence_for_mode(mode: str, jd_text: str, facts: Iterable[Fact]) -> list[dict]:
    facts = tuple(facts)
    if mode == "full_context":
        return [_fact_payload(fact) for fact in facts]
    if mode == "keyword_top5":
        return list(build_search_payload(jd_text, facts, limit=5)["matches"])
    raise ValueError(f"unknown context strategy: {mode}")


def build_messages(jd_text: str, evidence: list[dict]) -> list[dict]:
    system = (
        "You are evaluating which registered candidate facts are useful for a job description. "
        "Use only the supplied evidence. A related fact is not proof that every compound job "
        "requirement is satisfied. Respect every boundary. Do not invent technologies, metrics, "
        "responsibilities, production scope, or outcomes. Return strict JSON only."
    )
    user = (
        "Select only facts worth considering for a tailored resume. Return an object with this schema:\n"
        '{"selected":[{"fact_id":"...","relevance":"direct|partial",'
        '"reason":"brief reason","safe_claim":"one conservative evidence-grounded sentence"}],'
        '"unsupported_requirements":["..."]}\n\n'
        "JOB DESCRIPTION:\n"
        + jd_text
        + "\n\nAVAILABLE EVIDENCE:\n"
        + json.dumps(evidence, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_response(content: str) -> tuple[Selection, ...]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or not isinstance(payload.get("selected"), list):
        raise ValueError("response must contain a selected array")
    selections: list[Selection] = []
    seen: set[str] = set()
    for raw in payload["selected"]:
        if not isinstance(raw, dict):
            raise ValueError("every selected item must be an object")
        fact_id = str(raw.get("fact_id", "")).strip()
        relevance = str(raw.get("relevance", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        safe_claim = str(raw.get("safe_claim", "")).strip()
        if not fact_id or relevance not in {"direct", "partial"} or not reason or not safe_claim:
            raise ValueError("selected item has an invalid schema")
        if fact_id in seen:
            raise ValueError(f"duplicate selected fact_id: {fact_id}")
        seen.add(fact_id)
        selections.append(Selection(fact_id, relevance, reason, safe_claim))
    return tuple(selections)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def evaluate_run(
    mode: str,
    jd_text: str,
    facts: Iterable[Fact],
    labels: dict[str, dict[str, str]],
    *,
    llm_call: Callable[[list[dict]], str] = chat_completion,
) -> RunResult:
    facts = tuple(facts)
    known_ids = {fact.id for fact in facts}
    evidence = evidence_for_mode(mode, jd_text, facts)
    messages = build_messages(jd_text, evidence)
    prompt_chars = sum(len(str(message["content"])) for message in messages)
    started = time.monotonic()
    try:
        content = llm_call(messages)
        latency_ms = round((time.monotonic() - started) * 1000)
        selections = _parse_response(content)
    except Exception as exc:
        return RunResult(
            mode=mode,
            status="failed",
            selected=(),
            unknown_fact_ids=(),
            out_of_context_fact_ids=(),
            useful_selected=0,
            marginal_selected=0,
            irrelevant_selected=0,
            useful_total=sum(item.get("label") == "useful" for item in labels.values()),
            useful_recall=0.0,
            useful_precision=0.0,
            supported_precision=0.0,
            evidence_fact_count=len(evidence),
            prompt_chars=prompt_chars,
            response_chars=0,
            latency_ms=round((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )

    allowed_ids = {str(item["fact_id"]) for item in evidence}
    selected_ids = {item.fact_id for item in selections if item.fact_id in allowed_ids}
    unknown_ids = tuple(sorted({item.fact_id for item in selections if item.fact_id not in known_ids}))
    out_of_context_ids = tuple(
        sorted({item.fact_id for item in selections if item.fact_id in known_ids and item.fact_id not in allowed_ids})
    )
    useful = {fact_id for fact_id, item in labels.items() if item.get("label") == "useful"}
    marginal = {fact_id for fact_id, item in labels.items() if item.get("label") == "marginal"}
    irrelevant = {fact_id for fact_id, item in labels.items() if item.get("label") == "irrelevant"}
    useful_selected = len(selected_ids & useful)
    marginal_selected = len(selected_ids & marginal)
    irrelevant_selected = len(selected_ids & irrelevant)
    # Unknown and withheld fact IDs are invalid selections and therefore count
    # against precision even though they cannot receive a relevance label.
    selected_total = len(selections)
    return RunResult(
        mode=mode,
        status="completed",
        selected=selections,
        unknown_fact_ids=unknown_ids,
        out_of_context_fact_ids=out_of_context_ids,
        useful_selected=useful_selected,
        marginal_selected=marginal_selected,
        irrelevant_selected=irrelevant_selected,
        useful_total=len(useful),
        useful_recall=_ratio(useful_selected, len(useful)),
        useful_precision=_ratio(useful_selected, selected_total),
        supported_precision=_ratio(useful_selected + marginal_selected, selected_total),
        evidence_fact_count=len(evidence),
        prompt_chars=prompt_chars,
        response_chars=len(content),
        latency_ms=latency_ms,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_experiment(
    jd_paths: list[Path],
    facts_path: Path,
    labels_path: Path,
    *,
    llm_call: Callable[[list[dict]], str] = chat_completion,
) -> dict:
    facts = load_facts(facts_path)
    reviewer, label_cases = load_audit_labels(labels_path)
    cases: list[dict] = []
    for jd_path in jd_paths:
        if jd_path.name not in label_cases:
            raise ValueError(f"labels missing for JD: {jd_path.name}")
        jd_text = jd_path.read_text(encoding="utf-8")
        runs = [
            evaluate_run(mode, jd_text, facts, label_cases[jd_path.name], llm_call=llm_call)
            for mode in MODES
        ]
        cases.append(
            {
                "jd_file": jd_path.name,
                "jd_sha256": _sha256(jd_path),
                "runs": [asdict(run) for run in runs],
            }
        )
    return {
        "experiment": "full-context-vs-keyword-top5",
        "status": "single_run_decision_aid_not_benchmark",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "facts_sha256": _sha256(facts_path),
        "fact_count": len(facts),
        "labels_sha256": _sha256(labels_path),
        "label_reviewer": reviewer,
        "privacy": "Contains private JD names and generated claims; keep outside Git.",
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jd", action="append", required=True, type=Path, help="JD file; repeat for each case")
    parser.add_argument("--facts", type=Path, default=DEFAULT_FACTS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    payload = run_experiment(args.jd, args.facts, args.labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    completed = sum(run["status"] == "completed" for case in payload["cases"] for run in case["runs"])
    total = len(payload["cases"]) * len(MODES)
    print(f"Context strategy experiment complete: {completed}/{total} runs; private report: {args.output}")
    return 0 if completed == total else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic, data-driven regression evaluation for the Agent graph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from .agent.graph import build_agent
from .agent import prompts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "data" / "evaluation" / "agent_cases.json"


class ScenarioLLM:
    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case
        self.verifier = list(case["verifier"])
        self.verify_count = 0

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages):
        system = str(messages[0].content)
        if prompts.RETRIEVE_INSTRUCTION in system:
            # Skipping the tool deliberately exercises the graph's mandatory
            # deterministic search_facts fallback.
            return AIMessage(content="Use mandatory deterministic retrieval.")
        if prompts.GENERATE_INSTRUCTION in system:
            fact_ids = self.case["expected_fact_ids"]
            citation = f" [{fact_ids[0]}]" if fact_ids else ""
            return AIMessage(content=f"Scenario answer{citation}")
        if prompts.VERIFY_INSTRUCTION in system:
            index = min(self.verify_count, len(self.verifier) - 1)
            verdict = self.verifier[index]
            self.verify_count += 1
            if verdict == "PASS":
                return AIMessage(content='{"status":"PASS","unsupported_claims":[]}')
            if verdict == "FAIL":
                return AIMessage(content='{"status":"FAIL","unsupported_claims":["scenario rejection"]}')
            if verdict == "INVALID_SCHEMA":
                return AIMessage(content='{"status":"PASS","unsupported_claims":"not-a-list"}')
            return AIMessage(content="not-json")
        if prompts.REFLECT_INSTRUCTION in system:
            return AIMessage(content="Apply verifier feedback and retry.")
        raise AssertionError("unexpected Agent prompt")


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("agent eval file must contain a non-empty cases list")
    ids = [str(case.get("id", "")) for case in cases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("agent eval case IDs must be non-empty and unique")
    return cases


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    agent = build_agent(llm=ScenarioLLM(case))
    config = {"configurable": {"thread_id": f"eval-{case['id']}"}}
    nodes: list[str] = []
    for update in agent.stream(
        {
            "messages": [HumanMessage(str(case["query"]))],
            "turn": 0,
            "error_code": "",
            "verify_pass": False,
        },
        config=config,
        stream_mode="updates",
    ):
        nodes.extend(str(node) for node in update)
    state = agent.get_state(config).values
    evidence_ids = set(state.get("evidence_fact_ids", []))
    expected_ids = set(case["expected_fact_ids"])
    status = "completed" if state.get("verify_pass") else "blocked"
    retrieval_pass = expected_ids.issubset(evidence_ids)
    if not expected_ids:
        retrieval_pass = not evidence_ids
    outcome_pass = status == case["expected_status"]
    reflection_pass = nodes.count("reflect") == int(case["expected_reflections"])
    passed = retrieval_pass and outcome_pass and reflection_pass and not state.get("error_code")
    return {
        "id": case["id"],
        "passed": passed,
        "retrieval_pass": retrieval_pass,
        "outcome_pass": outcome_pass,
        "reflection_pass": reflection_pass,
        "expected_fact_ids": sorted(expected_ids),
        "actual_fact_ids": sorted(evidence_ids),
        "expected_status": case["expected_status"],
        "actual_status": status,
        "expected_reflections": case["expected_reflections"],
        "actual_reflections": nodes.count("reflect"),
        "nodes": nodes,
    }


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [evaluate_case(case) for case in cases]
    passed = sum(bool(item["passed"]) for item in results)
    return {
        "case_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "cases": results,
    }


def render(result: dict[str, Any]) -> str:
    lines = [
        "# Agent Regression Evaluation",
        "",
        f"- Cases: {result['case_count']}",
        f"- Passed: {result['passed']}",
        f"- Failed: {result['failed']}",
        f"- Pass rate: {result['pass_rate']:.0%}",
        "",
    ]
    for item in result["cases"]:
        mark = "PASS" if item["passed"] else "FAIL"
        lines.append(
            f"- [{mark}] {item['id']}: status={item['actual_status']}, "
            f"reflections={item['actual_reflections']}, facts={','.join(item['actual_fact_ids']) or 'none'}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(load_cases(args.cases))
    print(render(result))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Reproducible interview demo for Agent control paths.

The demo deliberately uses a scripted LLM and the normal graph/runtime. It
proves orchestration, fail-closed behavior, and trace visibility without an API
key; it does not evaluate model quality or production reliability.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import requests
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage

# Keep the default demo output focused on the three control paths. Callers can
# still opt into structured runtime logs by setting the variable explicitly.
os.environ.setdefault("RESUME_AGENT_LOG_LEVEL", "WARNING")

from .agent import prompts
from .agent.runtime import AgentInvocationError, AgentRuntime


class ScriptedDemoLLM:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages):
        system = str(messages[0].content)
        if self.scenario == "provider_timeout":
            raise requests.Timeout("scripted demo timeout")
        if prompts.RETRIEVE_INSTRUCTION in system:
            # Intentionally skip the tool. The graph must still perform its
            # deterministic fallback retrieval before generation.
            return AIMessage(content="Use mandatory deterministic retrieval.")
        if prompts.GENERATE_INSTRUCTION in system:
            if self.scenario == "verified_answer":
                return AIMessage(
                    content="有 Python REST API 数据自动化经历 [intern_data_automation]。"
                )
            return AIMessage(
                content="这是一条被脚本化 verifier 持续拒绝的演示回答 "
                "[intern_data_automation]。"
            )
        if prompts.VERIFY_INSTRUCTION in system:
            if self.scenario == "verified_answer":
                return AIMessage(content='{"status":"PASS","unsupported_claims":[]}')
            return AIMessage(
                content='{"status":"FAIL","unsupported_claims":["scripted demo rejection"]}'
            )
        if prompts.REFLECT_INSTRUCTION in system:
            return AIMessage(content="Apply verifier feedback and retry.")
        raise AssertionError("unexpected Agent prompt in scripted demo")


def _trace_summary(runtime: AgentRuntime, trace_id: str) -> dict:
    trace = runtime.get_trace(trace_id)
    if trace is None:
        raise AssertionError("demo trace was not persisted")
    return {
        "status": trace["status"],
        "verified": trace["verified"],
        "error_code": trace["error_code"],
        "events": [
            {
                "node": event["node"],
                "status": event["status"],
                "evidence_fact_ids": event["metadata"].get("evidence_fact_ids", []),
                "error_code": event["metadata"].get("error_code"),
            }
            for event in trace["events"]
        ],
    }


def _run_case(root: Path, scenario: str, message: str) -> dict:
    runtime = AgentRuntime(root / f"{scenario}.sqlite3", llm=ScriptedDemoLLM(scenario))
    try:
        conversation_id, _ = runtime.create_conversation()
        try:
            result = runtime.invoke(conversation_id, message, request_id=f"demo-{scenario}")
        except AgentInvocationError as exc:
            return {
                "scenario": scenario,
                "outcome": "structured_error",
                "error_code": exc.code,
                "http_status": exc.http_status,
                "retryable": exc.retryable,
                "trace": _trace_summary(runtime, exc.trace_id),
            }
        return {
            "scenario": scenario,
            "outcome": result.status,
            "verified": result.verified,
            "nodes": list(result.nodes),
            "trace": _trace_summary(runtime, result.trace_id),
        }
    finally:
        runtime.close()


def run_demo() -> dict:
    with TemporaryDirectory(prefix="truthful-resume-agent-demo-") as temp_dir:
        root = Path(temp_dir)
        cases = [
            _run_case(root, "verified_answer", "哪些事实支持 Python REST API 经历？"),
            _run_case(root, "verifier_blocked", "演示 verifier 持续失败后的状态。"),
            _run_case(root, "provider_timeout", "演示模型依赖超时。"),
        ]
    return {
        "demo": "agent-control-paths",
        "provider": "scripted",
        "claim": "Demonstrates orchestration and failure boundaries, not model quality.",
        "cases": cases,
    }


def validate_demo(payload: dict) -> None:
    by_scenario = {case["scenario"]: case for case in payload["cases"]}
    verified = by_scenario["verified_answer"]
    blocked = by_scenario["verifier_blocked"]
    timeout = by_scenario["provider_timeout"]
    if not (verified["outcome"] == "completed" and verified["verified"]):
        raise AssertionError("verified demo path did not complete")
    if not (blocked["outcome"] == "blocked" and not blocked["verified"]):
        raise AssertionError("verifier exhaustion was not blocked")
    if [event["node"] for event in blocked["trace"]["events"]].count("reflect") != 3:
        raise AssertionError("blocked demo did not traverse three reflection nodes")
    if not (
        timeout["outcome"] == "structured_error"
        and timeout["error_code"] == "LLM_TIMEOUT"
        and timeout["http_status"] == 504
        and timeout["retryable"] is True
    ):
        raise AssertionError("timeout demo did not return the structured contract")


def render_text(payload: dict) -> str:
    lines = [
        "Truthful Resume Agent · Offline Control-Path Demo",
        "Provider: scripted (no API key; this is not a model-quality evaluation)",
        "",
    ]
    for index, case in enumerate(payload["cases"], start=1):
        events = case["trace"]["events"]
        nodes = " -> ".join(event["node"] for event in events) or "no completed node"
        detail = case.get("error_code") or f"verified={case.get('verified', False)}"
        lines.append(f"{index}. {case['scenario']}: {case['outcome']} ({detail})")
        lines.append(f"   trace: {nodes}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the sanitized JSON payload")
    args = parser.parse_args(argv)
    payload = run_demo()
    validate_demo(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

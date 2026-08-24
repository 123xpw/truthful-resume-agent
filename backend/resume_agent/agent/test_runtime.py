"""Reliable Agent API/runtime tests using deterministic fake models."""

from __future__ import annotations

import json
import importlib
import os
from pathlib import Path
import requests
from tempfile import TemporaryDirectory

os.environ.setdefault("RESUME_AGENT_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from ..llm_client import LLMServiceError, call_with_retry, classify_llm_error
from ..web.app import app, get_agent_runtime
from . import graph, prompts
from .runtime import AgentInvocationError, AgentRuntime


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class StableFakeLLM:
    def __init__(self, verifier_responses: list[str] | None = None) -> None:
        self.verifier_responses = list(verifier_responses or ['{"status":"PASS","unsupported_claims":[]}'])
        self.calls: list[str] = []

    def bind_tools(self, _tools):
        return self

    def invoke(self, messages):
        system = str(messages[0].content)
        if prompts.RETRIEVE_INSTRUCTION in system:
            self.calls.append("retrieve")
            return AIMessage(content="Use deterministic fallback retrieval.")
        if prompts.GENERATE_INSTRUCTION in system:
            self.calls.append("generate")
            return AIMessage(content="Used Python for data automation [intern_data_automation].")
        if prompts.VERIFY_INSTRUCTION in system:
            self.calls.append("verify")
            content = self.verifier_responses.pop(0) if len(self.verifier_responses) > 1 else self.verifier_responses[0]
            return AIMessage(content=content)
        if prompts.REFLECT_INSTRUCTION in system:
            self.calls.append("reflect")
            return AIMessage(content="Remove unsupported wording and retry.")
        raise AssertionError("unexpected prompt")


def _runtime(temp_dir: str, llm=None) -> AgentRuntime:
    return AgentRuntime(Path(temp_dir) / "runtime.sqlite3", llm=llm or StableFakeLLM())


def test_conversation_is_persisted() -> None:
    with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
        runtime = _runtime(temp_dir)
        try:
            conversation_id, _ = runtime.create_conversation()
            _assert(runtime.conversation_exists(conversation_id), "conversation was not persisted")
        finally:
            runtime.close()


def test_conversation_survives_runtime_restart() -> None:
    with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
        db_path = Path(temp_dir) / "runtime.sqlite3"
        first = AgentRuntime(db_path, llm=StableFakeLLM())
        conversation_id, _ = first.create_conversation()
        first.close()
        second = AgentRuntime(db_path, llm=StableFakeLLM())
        try:
            _assert(second.conversation_exists(conversation_id), "conversation did not survive restart")
        finally:
            second.close()


def test_thread_memory_is_isolated() -> None:
    with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
        runtime = _runtime(temp_dir)
        try:
            first_id, _ = runtime.create_conversation()
            second_id, _ = runtime.create_conversation()
            runtime.invoke(first_id, "first-private-marker", request_id="request-a")
            runtime.invoke(second_id, "second-private-marker", request_id="request-b")
            first_state = runtime.graph.get_state({"configurable": {"thread_id": first_id}}).values
            second_state = runtime.graph.get_state({"configurable": {"thread_id": second_id}}).values
            first_human = [str(item.content) for item in first_state["messages"] if isinstance(item, HumanMessage)]
            second_human = [str(item.content) for item in second_state["messages"] if isinstance(item, HumanMessage)]
            _assert("first-private-marker" in first_human, "first thread lost its message")
            _assert("first-private-marker" not in second_human, "first thread leaked into second")
            _assert("second-private-marker" not in first_human, "second thread leaked into first")
        finally:
            runtime.close()


def test_same_thread_keeps_multi_turn_context() -> None:
    with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
        runtime = _runtime(temp_dir)
        try:
            conversation_id, _ = runtime.create_conversation()
            runtime.invoke(conversation_id, "turn-one-marker", request_id="request-1")
            runtime.invoke(conversation_id, "turn-two-marker", request_id="request-2")
            state = runtime.graph.get_state({"configurable": {"thread_id": conversation_id}}).values
            human = [str(item.content) for item in state["messages"] if isinstance(item, HumanMessage)]
            _assert("turn-one-marker" in human and "turn-two-marker" in human, "multi-turn context missing")
        finally:
            runtime.close()


def test_trace_records_nodes_without_raw_message() -> None:
    marker = "do-not-store-this-private-message"
    with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
        runtime = _runtime(temp_dir)
        try:
            conversation_id, _ = runtime.create_conversation()
            result = runtime.invoke(conversation_id, marker, request_id="request-trace")
            trace = runtime.get_trace(result.trace_id)
            _assert(trace is not None, "trace was not stored")
            _assert([item["node"] for item in trace["events"]] == ["retrieve", "generate", "verify"], "node trace mismatch")
            _assert(marker not in json.dumps(trace, ensure_ascii=False), "raw user message leaked into trace")
        finally:
            runtime.close()


def test_verifier_exhaustion_returns_blocked() -> None:
    failures = ['{"status":"FAIL","unsupported_claims":["unsupported"]}'] * 4
    with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
        runtime = _runtime(temp_dir, StableFakeLLM(failures))
        try:
            conversation_id, _ = runtime.create_conversation()
            result = runtime.invoke(conversation_id, "blocked case", request_id="request-blocked")
            _assert(result.status == "blocked" and not result.verified, "failed verifier was not blocked")
            _assert(result.nodes.count("reflect") == 3, "bounded reflection loop changed")
        finally:
            runtime.close()


def test_transient_llm_call_retries_then_succeeds() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise requests.Timeout("private provider details")
        return "ok"

    result = call_with_retry(operation, max_retries=2, sleep=lambda _seconds: None)
    _assert(result == "ok" and attempts == 3, "transient call did not use bounded retries")


def test_auth_failure_is_not_retryable() -> None:
    class AuthFailure(RuntimeError):
        status_code = 401

    error = classify_llm_error(AuthFailure("secret response body"))
    _assert(error.code == "LLM_AUTH_ERROR", "auth failure code mismatch")
    _assert(not error.retryable, "auth failure must not be retried")
    _assert("secret response body" not in str(error), "provider response leaked")


def test_llm_timeout_becomes_structured_service_error() -> None:
    class TimeoutLLM:
        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            raise requests.Timeout("provider detail")

    old_retries = os.environ.get("RESUME_AGENT_LLM_MAX_RETRIES")
    os.environ["RESUME_AGENT_LLM_MAX_RETRIES"] = "0"
    try:
        with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
            runtime = _runtime(temp_dir, TimeoutLLM())
            try:
                conversation_id, _ = runtime.create_conversation()
                try:
                    runtime.invoke(conversation_id, "timeout case", request_id="request-timeout")
                except AgentInvocationError as exc:
                    _assert(exc.code == "LLM_TIMEOUT" and exc.http_status == 504, "timeout mapping mismatch")
                    trace = runtime.get_trace(exc.trace_id)
                    _assert(trace is not None and trace["error_code"] == "LLM_TIMEOUT", "timeout trace missing")
                else:
                    raise AssertionError("timeout unexpectedly succeeded")
            finally:
                runtime.close()
    finally:
        if old_retries is None:
            os.environ.pop("RESUME_AGENT_LLM_MAX_RETRIES", None)
        else:
            os.environ["RESUME_AGENT_LLM_MAX_RETRIES"] = old_retries


def test_fact_tool_failure_fails_closed() -> None:
    @tool
    def broken_search_facts(query: str) -> str:
        """Always fail for a deterministic fault-injection test."""
        raise OSError(f"unavailable for query length {len(query)}")

    original = graph.search_facts
    graph.search_facts = broken_search_facts
    try:
        with TemporaryDirectory(prefix="resume-agent-runtime-") as temp_dir:
            runtime = _runtime(temp_dir)
            try:
                conversation_id, _ = runtime.create_conversation()
                try:
                    runtime.invoke(conversation_id, "tool failure", request_id="request-tool")
                except AgentInvocationError as exc:
                    _assert(exc.code == "FACT_TOOL_UNAVAILABLE", "tool failure code mismatch")
                    _assert("generate" not in [item["node"] for item in runtime.get_trace(exc.trace_id)["events"]], "generation continued without facts")
                else:
                    raise AssertionError("tool failure unexpectedly succeeded")
            finally:
                runtime.close()
    finally:
        graph.search_facts = original


def test_fastapi_agent_contract() -> None:
    with TemporaryDirectory(prefix="resume-agent-api-") as temp_dir:
        runtime = _runtime(temp_dir)
        app.dependency_overrides[get_agent_runtime] = lambda: runtime
        try:
            with TestClient(app) as client:
                created = client.post("/api/v1/conversations")
                _assert(created.status_code == 201, "conversation endpoint failed")
                conversation_id = created.json()["conversation_id"]
                response = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"message": "Python data automation"},
                )
                _assert(response.status_code == 200, "message endpoint failed")
                payload = response.json()
                _assert(payload["verified"] is True and payload["status"] == "completed", "API result mismatch")
                _assert(response.headers.get("X-Request-ID"), "request ID header missing")
                trace = client.get(f"/api/v1/traces/{payload['trace_id']}")
                _assert(trace.status_code == 200 and len(trace.json()["events"]) == 3, "trace endpoint failed")
                missing = client.post(
                    "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages",
                    json={"message": "test"},
                )
                _assert(missing.status_code == 404, "unknown conversation did not return 404")
        finally:
            app.dependency_overrides.clear()
            runtime.close()


def test_semantic_retrieval_has_explicit_degradation() -> None:
    web_module = importlib.import_module("backend.resume_agent.web.app")
    original_analyze = web_module.analyze_jd
    original_save = web_module.save_jd_memory

    def failing_semantic(jd_text: str, matcher: str = "keyword"):
        if matcher == "semantic":
            raise OSError("private qdrant path")
        return original_analyze(jd_text, matcher="keyword")

    with TemporaryDirectory(prefix="resume-agent-api-") as temp_dir:
        web_module.analyze_jd = failing_semantic
        web_module.save_jd_memory = lambda *_args, **_kwargs: Path(temp_dir) / "jd.md"
        try:
            with TestClient(app) as client:
                degraded = client.post(
                    "/api/analyze",
                    json={
                        "jd_text": "Python REST API",
                        "name": "fallback-test",
                        "matcher": "semantic",
                    },
                )
                payload = degraded.json()
                _assert(degraded.status_code == 200, "semantic fallback request failed")
                _assert(payload["degraded"] is True and payload["used_matcher"] == "keyword", "degradation was not explicit")
                _assert(payload["warnings"][0]["code"] == "SEMANTIC_RETRIEVAL_UNAVAILABLE", "degradation code missing")
                blocked = client.post(
                    "/api/analyze",
                    json={
                        "jd_text": "Python REST API",
                        "name": "no-fallback-test",
                        "matcher": "semantic",
                        "fallback_to_keyword": False,
                    },
                )
                _assert(blocked.status_code == 503, "disabled fallback did not return 503")
        finally:
            web_module.analyze_jd = original_analyze
            web_module.save_jd_memory = original_save


ALL_TESTS = [
    test_conversation_is_persisted,
    test_conversation_survives_runtime_restart,
    test_thread_memory_is_isolated,
    test_same_thread_keeps_multi_turn_context,
    test_trace_records_nodes_without_raw_message,
    test_verifier_exhaustion_returns_blocked,
    test_transient_llm_call_retries_then_succeeds,
    test_auth_failure_is_not_retryable,
    test_llm_timeout_becomes_structured_service_error,
    test_fact_tool_failure_fails_closed,
    test_fastapi_agent_contract,
    test_semantic_retrieval_has_explicit_degradation,
]


def run_all() -> None:
    for test in ALL_TESTS:
        test()


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项 runtime/API 测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

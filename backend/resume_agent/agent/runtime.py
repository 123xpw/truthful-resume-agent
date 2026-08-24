"""Persistent, observable runtime for the read-only conversational Agent."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from .graph import build_agent
from .observability import TraceStore, log_event


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME_DB = PROJECT_ROOT / "data" / "agent_runtime.sqlite3"
DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 90.0


def get_workflow_timeout_seconds() -> float:
    raw = os.environ.get("RESUME_AGENT_WORKFLOW_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_WORKFLOW_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_WORKFLOW_TIMEOUT_SECONDS
    return min(max(value, 5.0), 300.0)


class AgentInvocationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        trace_id: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.trace_id = trace_id
        self.retryable = retryable


@dataclass(frozen=True)
class AgentRunResult:
    conversation_id: str
    trace_id: str
    status: str
    answer: str
    verified: bool
    degraded: bool
    nodes: tuple[str, ...]


def _http_status_for_error(code: str) -> int:
    if code == "LLM_TIMEOUT" or code == "AGENT_TIMEOUT":
        return 504
    if code in {
        "LLM_NOT_CONFIGURED",
        "LLM_AUTH_ERROR",
        "LLM_RATE_LIMIT",
        "LLM_UNAVAILABLE",
        "FACT_TOOL_UNAVAILABLE",
    }:
        return 503
    if code in {"LLM_REQUEST_INVALID", "LLM_INVALID_RESPONSE", "UNKNOWN_TOOL"}:
        return 502
    return 500


class AgentRuntime:
    """Owns one lightweight SQLite checkpointer and its sanitized trace store."""

    def __init__(self, db_path: Path = DEFAULT_RUNTIME_DB, *, llm=None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
        self.trace_store = TraceStore(self.db_path)
        self._checkpoint_connection = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            check_same_thread=False,
        )
        self._checkpoint_connection.execute("PRAGMA busy_timeout = 10000")
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.checkpointer.setup()
        self.graph = build_agent(checkpointer=self.checkpointer, llm=llm)
        # SqliteSaver is thread-safe internally, but serializing a complete
        # local run also keeps node traces ordered and predictable.
        self._run_lock = threading.RLock()

    def close(self) -> None:
        self._checkpoint_connection.close()

    def create_conversation(self) -> tuple[str, str]:
        conversation_id = str(uuid4())
        created_at = self.trace_store.create_conversation(conversation_id)
        log_event("agent.conversation.created", conversation_id=conversation_id)
        return conversation_id, created_at

    def conversation_exists(self, conversation_id: str) -> bool:
        return self.trace_store.conversation_exists(conversation_id)

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        return self.trace_store.get_trace(trace_id)

    def invoke(self, conversation_id: str, message: str, *, request_id: str) -> AgentRunResult:
        trace_id = str(uuid4())
        self.trace_store.start_trace(trace_id, request_id, conversation_id)
        log_event(
            "agent.run.started",
            request_id=request_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            message_length=len(message),
        )
        started = time.monotonic()
        previous = started
        nodes: list[str] = []
        config = {"configurable": {"thread_id": conversation_id}}
        graph_input = {
            "messages": [HumanMessage(message)],
            "draft": "",
            "evidence": "",
            "evidence_fact_ids": [],
            "verification_feedback": "",
            "verify_pass": False,
            "turn": 0,
            "degraded": False,
            "error_code": "",
            "error_message": "",
            "error_retryable": False,
        }

        try:
            with self._run_lock:
                for update in self.graph.stream(graph_input, config=config, stream_mode="updates"):
                    now = time.monotonic()
                    if now - started > get_workflow_timeout_seconds():
                        raise AgentInvocationError(
                            "AGENT_TIMEOUT",
                            "Agent workflow exceeded its time budget.",
                            http_status=504,
                            trace_id=trace_id,
                            retryable=True,
                        )
                    for node, values in update.items():
                        nodes.append(str(node))
                        metadata: dict[str, Any] = {}
                        if isinstance(values, dict):
                            fact_ids = values.get("evidence_fact_ids")
                            if isinstance(fact_ids, list):
                                metadata["evidence_fact_ids"] = [str(item) for item in fact_ids]
                            if values.get("error_code"):
                                metadata["error_code"] = str(values["error_code"])
                            if "verify_pass" in values:
                                metadata["verified"] = bool(values["verify_pass"])
                            if "turn" in values:
                                metadata["turn"] = int(values["turn"])
                        duration_ms = round((now - previous) * 1000)
                        previous = now
                        node_status = "failed" if metadata.get("error_code") else "completed"
                        self.trace_store.add_event(
                            trace_id,
                            len(nodes),
                            str(node),
                            node_status,
                            duration_ms,
                            metadata,
                        )
                        log_event(
                            "agent.node.completed",
                            request_id=request_id,
                            conversation_id=conversation_id,
                            trace_id=trace_id,
                            node=str(node),
                            status=node_status,
                            duration_ms=duration_ms,
                            **metadata,
                        )
                state = self.graph.get_state(config).values
        except AgentInvocationError as exc:
            self.trace_store.finish_trace(
                trace_id,
                status="failed",
                verified=False,
                degraded=False,
                error_code=exc.code,
            )
            log_event(
                "agent.run.failed",
                request_id=request_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                error_code=exc.code,
                retryable=exc.retryable,
            )
            raise
        except Exception as exc:
            self.trace_store.finish_trace(
                trace_id,
                status="failed",
                verified=False,
                degraded=False,
                error_code="AGENT_INTERNAL_ERROR",
            )
            log_event(
                "agent.run.failed",
                request_id=request_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                error_code="AGENT_INTERNAL_ERROR",
                exception_type=type(exc).__name__,
            )
            raise AgentInvocationError(
                "AGENT_INTERNAL_ERROR",
                "Agent workflow failed unexpectedly.",
                http_status=500,
                trace_id=trace_id,
                retryable=False,
            ) from exc

        error_code = str(state.get("error_code", "") or "")
        verified = bool(state.get("verify_pass", False))
        degraded = bool(state.get("degraded", False))
        if error_code:
            retryable = bool(state.get("error_retryable", False))
            message_safe = str(state.get("error_message", "Agent dependency failed."))
            self.trace_store.finish_trace(
                trace_id,
                status="failed",
                verified=False,
                degraded=degraded,
                error_code=error_code,
            )
            log_event(
                "agent.run.failed",
                request_id=request_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                error_code=error_code,
                retryable=retryable,
            )
            raise AgentInvocationError(
                error_code,
                message_safe,
                http_status=_http_status_for_error(error_code),
                trace_id=trace_id,
                retryable=retryable,
            )

        status = "completed" if verified else "blocked"
        self.trace_store.finish_trace(
            trace_id,
            status=status,
            verified=verified,
            degraded=degraded,
            error_code=None,
        )
        log_event(
            "agent.run.finished",
            request_id=request_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            status=status,
            verified=verified,
            degraded=degraded,
            duration_ms=round((time.monotonic() - started) * 1000),
            node_count=len(nodes),
        )
        return AgentRunResult(
            conversation_id=conversation_id,
            trace_id=trace_id,
            status=status,
            answer=str(state.get("draft", "") or ""),
            verified=verified,
            degraded=degraded,
            nodes=tuple(nodes),
        )

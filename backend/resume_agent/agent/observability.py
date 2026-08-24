"""Privacy-conscious structured logs and local Agent trace storage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any


LOGGER = logging.getLogger("truthful_resume_agent")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_logging() -> None:
    """Configure one JSON-lines handler without touching the root logger."""
    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
    level_name = os.environ.get("RESUME_AGENT_LOG_LEVEL", "INFO").upper()
    LOGGER.setLevel(getattr(logging, level_name, logging.INFO))
    LOGGER.propagate = False


def log_event(event: str, **fields: Any) -> None:
    """Emit metadata only; callers must never pass raw JD/resume/message text."""
    configure_logging()
    payload = {"timestamp": utc_now(), "event": event, **fields}
    LOGGER.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


class TraceStore:
    """Small SQLite store for conversations and sanitized node-level traces."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_traces (
                    trace_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    degraded INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    FOREIGN KEY(conversation_id) REFERENCES agent_conversations(conversation_id)
                );

                CREATE TABLE IF NOT EXISTS agent_trace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    node TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(trace_id) REFERENCES agent_traces(trace_id)
                );

                CREATE INDEX IF NOT EXISTS idx_agent_trace_events_trace
                    ON agent_trace_events(trace_id, sequence_no);
                """
            )

    def create_conversation(self, conversation_id: str) -> str:
        created_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_conversations(conversation_id, created_at) VALUES (?, ?)",
                (conversation_id, created_at),
            )
        return created_at

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agent_conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return row is not None

    def start_trace(self, trace_id: str, request_id: str, conversation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_traces(
                    trace_id, request_id, conversation_id, started_at, status
                ) VALUES (?, ?, ?, ?, 'running')
                """,
                (trace_id, request_id, conversation_id, utc_now()),
            )

    def add_event(
        self,
        trace_id: str,
        sequence_no: int,
        node: str,
        status: str,
        duration_ms: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_metadata = metadata or {}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_trace_events(
                    trace_id, sequence_no, node, status, duration_ms, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    sequence_no,
                    node,
                    status,
                    max(0, int(duration_ms)),
                    json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

    def finish_trace(
        self,
        trace_id: str,
        *,
        status: str,
        verified: bool,
        degraded: bool,
        error_code: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_traces
                SET finished_at = ?, status = ?, verified = ?, degraded = ?, error_code = ?
                WHERE trace_id = ?
                """,
                (utc_now(), status, int(verified), int(degraded), error_code, trace_id),
            )

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            trace = connection.execute(
                "SELECT * FROM agent_traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if trace is None:
                return None
            events = connection.execute(
                """
                SELECT sequence_no, node, status, duration_ms, metadata_json
                FROM agent_trace_events
                WHERE trace_id = ?
                ORDER BY sequence_no, id
                """,
                (trace_id,),
            ).fetchall()
        return {
            "trace_id": trace["trace_id"],
            "request_id": trace["request_id"],
            "conversation_id": trace["conversation_id"],
            "started_at": trace["started_at"],
            "finished_at": trace["finished_at"],
            "status": trace["status"],
            "verified": bool(trace["verified"]),
            "degraded": bool(trace["degraded"]),
            "error_code": trace["error_code"],
            "events": [
                {
                    "sequence": event["sequence_no"],
                    "node": event["node"],
                    "status": event["status"],
                    "duration_ms": event["duration_ms"],
                    "metadata": json.loads(event["metadata_json"]),
                }
                for event in events
            ],
        }

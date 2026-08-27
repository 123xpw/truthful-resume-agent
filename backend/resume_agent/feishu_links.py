"""Local links between Feishu ledger rows and resume workflow applications.

Links are local-only metadata. They never modify the Feishu spreadsheet and
are content-bound so a reused or materially edited ledger row becomes stale.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .analyzer import slugify
from .feishu_analysis import extract_feishu_records
from .feishu_sync import FEISHU_SOURCE_ID, feishu_sync_status
from .outcomes import create_outcome_backup, default_outcome_path, resolve_resume_ref
from .status import inspect_application, list_applications, status_stage


_SEQUENCE_RE = re.compile(r"^[1-9][0-9]{0,8}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _database_path(project_root: Path) -> Path:
    outcome_path = default_outcome_path(project_root)
    return outcome_path if outcome_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} else project_root / "data" / "feishu_sync.sqlite3"


def _schema_exists(path: Path) -> bool:
    if not path.is_file():
        return False
    with sqlite3.connect(path, timeout=5.0) as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='feishu_application_links'"
        ).fetchone() is not None


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feishu_application_links (
            source_id TEXT NOT NULL,
            sequence TEXT NOT NULL,
            application_name TEXT NOT NULL,
            resume_ref TEXT,
            resume_sha256_at_link TEXT,
            row_identity_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            PRIMARY KEY(source_id, sequence)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feishu_links_application
        ON feishu_application_links(application_name)
        """
    )


def _prepare_store(project_root: Path) -> Path:
    path = _database_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not _schema_exists(path):
        create_outcome_backup(path)
    with sqlite3.connect(path, timeout=5.0) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        _initialize_schema(connection)
        connection.commit()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _ledger_records(project_root: Path) -> list[dict[str, Any]]:
    sync = feishu_sync_status(project_root)
    return extract_feishu_records(sync.get("values", []))


def _record_by_sequence(project_root: Path, sequence: str) -> dict[str, Any]:
    if not _SEQUENCE_RE.fullmatch(sequence):
        raise ValueError("Feishu sequence must be a positive integer string")
    matches = [record for record in _ledger_records(project_root) if record["sequence"] == sequence]
    if not matches:
        raise ValueError("Feishu sequence does not exist in the latest local snapshot")
    if len(matches) > 1:
        raise ValueError("Feishu sequence is not unique in the latest local snapshot")
    return matches[0]


def _valid_application(project_root: Path, application_name: str) -> str:
    canonical = slugify(application_name)
    known = {status.name for status in list_applications(project_root)}
    if canonical not in known:
        raise ValueError("local application does not exist")
    return canonical


def _resume_digest(project_root: Path, resume_ref: str | None) -> str | None:
    path = resolve_resume_ref(project_root, resume_ref)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else None


def _canonical_summary(project_root: Path, application_name: str) -> dict[str, Any]:
    path = project_root / "data" / "outputs" / application_name / "canonical_audit.json"
    if not path.is_file():
        return {"available": False, "ready": False, "pdf_sha256": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": True, "ready": False, "pdf_sha256": None}
    return {
        "available": True,
        "ready": bool(payload.get("ready")),
        "pdf_sha256": payload.get("pdf_sha256") if isinstance(payload.get("pdf_sha256"), str) else None,
    }


def _link_payload(project_root: Path, row: sqlite3.Row, record: dict[str, Any] | None) -> dict[str, Any]:
    resume_ref = str(row["resume_ref"]) if row["resume_ref"] else None
    current_resume_sha256: str | None = None
    artifact_missing = False
    if resume_ref:
        try:
            current_resume_sha256 = _resume_digest(project_root, resume_ref)
        except ValueError:
            artifact_missing = True
    application_name = str(row["application_name"])
    known_applications = {status.name for status in list_applications(project_root)}
    try:
        if application_name not in known_applications:
            raise ValueError("local application no longer exists")
        application_state = status_stage(inspect_application(project_root, application_name))
    except (OSError, ValueError):
        application_state = "missing"
    recorded_sha256 = str(row["resume_sha256_at_link"]) if row["resume_sha256_at_link"] else None
    canonical = _canonical_summary(project_root, application_name)
    canonical_pdf_matches_current: bool | None = None
    if canonical["pdf_sha256"] and current_resume_sha256:
        canonical_pdf_matches_current = canonical["pdf_sha256"] == current_resume_sha256
    return {
        "sequence": str(row["sequence"]),
        "application_name": application_name,
        "resume_ref": resume_ref,
        "resume_sha256_at_link": recorded_sha256,
        "current_resume_sha256": current_resume_sha256,
        "artifact_missing": artifact_missing,
        "artifact_changed": bool(recorded_sha256 and current_resume_sha256 and recorded_sha256 != current_resume_sha256),
        "row_stale": record is None or str(row["row_identity_sha256"]) != record["row_identity_sha256"],
        "application_state": application_state,
        "canonical": canonical,
        "canonical_pdf_matches_current": canonical_pdf_matches_current,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def list_feishu_application_links(project_root: Path) -> dict[str, Any]:
    records = _ledger_records(project_root)
    record_map = {record["sequence"]: record for record in records}
    path = _database_path(project_root)
    if not _schema_exists(path):
        links: list[dict[str, Any]] = []
    else:
        connection = sqlite3.connect(path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT * FROM feishu_application_links
                WHERE source_id = ? AND archived_at IS NULL
                ORDER BY CAST(sequence AS INTEGER)
                """,
                (FEISHU_SOURCE_ID,),
            ).fetchall()
            links = [_link_payload(project_root, row, record_map.get(str(row["sequence"]))) for row in rows]
        finally:
            connection.close()
    linked_sequences = {link["sequence"] for link in links}
    return {
        "records": records,
        "links": links,
        "record_count": len(records),
        "linked_count": len(links),
        "unlinked_count": sum(record["sequence"] not in linked_sequences for record in records),
    }


def save_feishu_application_link(
    project_root: Path,
    *,
    sequence: str,
    application_name: str,
    resume_ref: str | None,
) -> dict[str, Any]:
    record = _record_by_sequence(project_root, sequence)
    application = _valid_application(project_root, application_name)
    digest = _resume_digest(project_root, resume_ref)
    path = _prepare_store(project_root)
    create_outcome_backup(path)
    now = _utc_now()
    with sqlite3.connect(path, timeout=5.0) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            """
            INSERT INTO feishu_application_links(
                source_id, sequence, application_name, resume_ref,
                resume_sha256_at_link, row_identity_sha256, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, sequence) DO UPDATE SET
                application_name=excluded.application_name,
                resume_ref=excluded.resume_ref,
                resume_sha256_at_link=excluded.resume_sha256_at_link,
                row_identity_sha256=excluded.row_identity_sha256,
                updated_at=excluded.updated_at,
                archived_at=NULL
            """,
            (
                FEISHU_SOURCE_ID,
                sequence,
                application,
                resume_ref,
                digest,
                record["row_identity_sha256"],
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT * FROM feishu_application_links
            WHERE source_id = ? AND sequence = ? AND archived_at IS NULL
            """,
            (FEISHU_SOURCE_ID, sequence),
        ).fetchone()
    return _link_payload(project_root, row, record)


def delete_feishu_application_link(project_root: Path, sequence: str) -> None:
    if not _SEQUENCE_RE.fullmatch(sequence):
        raise ValueError("Feishu sequence must be a positive integer string")
    path = _database_path(project_root)
    if not _schema_exists(path):
        raise ValueError("Feishu application link does not exist")
    create_outcome_backup(path)
    with sqlite3.connect(path, timeout=5.0) as connection:
        cursor = connection.execute(
            """
            UPDATE feishu_application_links
            SET archived_at = ?, updated_at = ?
            WHERE source_id = ? AND sequence = ? AND archived_at IS NULL
            """,
            (_utc_now(), _utc_now(), FEISHU_SOURCE_ID, sequence),
        )
        if cursor.rowcount != 1:
            raise ValueError("Feishu application link does not exist")
        connection.commit()

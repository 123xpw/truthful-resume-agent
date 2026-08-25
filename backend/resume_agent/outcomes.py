from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from uuid import uuid4

from .delivery import default_delivery_root
from .io_utils import atomic_write_text


VALID_OUTCOMES = {
    "applied",
    "assessment",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "unknown",
}

OUTCOME_SCHEMA_VERSION = 1
OUTCOME_BACKUP_LIMIT = 10
_OUTCOME_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class OutcomeEvent:
    application: str
    status: str
    date: str
    resume_sha256: str | None
    resume_path: str | None
    note: str
    event_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived_at: str | None = None


@dataclass(frozen=True)
class ResumeArtifact:
    ref: str
    label: str
    source: str
    state: str
    filename: str
    application_hint: str
    modified_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _project_setting(project_root: Path, name: str, default: str = "") -> str:
    configured = os.environ.get(name)
    if configured:
        return configured
    env_path = project_root / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() == name and value.strip():
                return value.strip()
    return default


def default_outcome_path(project_root: Path) -> Path:
    configured = _project_setting(project_root, "RESUME_AGENT_OUTCOME_PATH")
    if configured:
        configured_path = Path(configured)
        return configured_path if configured_path.is_absolute() else project_root / configured_path
    return project_root / "data" / "application_tracker.sqlite3"


def default_legacy_outcome_path(project_root: Path) -> Path:
    return project_root / "data" / "application_outcomes.json"


def _is_json_store(path: Path) -> bool:
    return path.suffix.lower() == ".json"


def _resume_hash(
    project_root: Path,
    application: str,
    resume_path: Path | None = None,
    *,
    use_default_resume: bool = True,
) -> tuple[str | None, str | None]:
    path = resume_path
    if path is None and use_default_resume:
        path = project_root / "data" / "outputs" / application / "resume_draft.pdf"
    if path is None:
        return None, None
    resolved = str(path.resolve()) if path.exists() else None
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return digest, resolved


def _legacy_event_id(item: dict, index: int) -> str:
    encoded = json.dumps(
        {
            "application": item.get("application"),
            "status": item.get("status"),
            "date": item.get("date"),
            "resume_sha256": item.get("resume_sha256"),
            "resume_path": item.get("resume_path"),
            "note": item.get("note", ""),
            "index": index,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"legacy-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


def _validate_status_and_date(status: str, event_date: str | None) -> str:
    if status not in VALID_OUTCOMES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_OUTCOMES))}")
    effective_date = event_date or date.today().isoformat()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise ValueError("event date must use YYYY-MM-DD") from exc
    return effective_date


def _load_json_outcomes(path: Path) -> list[OutcomeEvent]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("outcome JSON must contain a list")
    events: list[OutcomeEvent] = []
    for index, item in enumerate(raw):
        now = _utc_now()
        events.append(
            OutcomeEvent(
                application=str(item["application"]),
                status=str(item["status"]),
                date=str(item["date"]),
                resume_sha256=str(item["resume_sha256"]) if item.get("resume_sha256") else None,
                resume_path=str(item["resume_path"]) if item.get("resume_path") else None,
                note=str(item.get("note", "")),
                event_id=str(item.get("event_id") or _legacy_event_id(item, index)),
                created_at=str(item.get("created_at") or now),
                updated_at=str(item.get("updated_at") or item.get("created_at") or now),
                archived_at=str(item["archived_at"]) if item.get("archived_at") else None,
            )
        )
    return events


def _legacy_candidates(database_path: Path) -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("RESUME_AGENT_LEGACY_OUTCOME_PATH")
    if configured:
        candidates.append(Path(configured))
    candidates.append(database_path.with_name("application_outcomes.json"))
    if database_path.parent.name == "runtime":
        candidates.append(database_path.parent.parent / "application_outcomes.json")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _initialize_schema(connection: sqlite3.Connection) -> None:
    status_values = ", ".join(f"'{status}'" for status in sorted(VALID_OUTCOMES))
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS outcome_events (
            event_id TEXT PRIMARY KEY,
            application TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({status_values})),
            event_date TEXT NOT NULL,
            resume_sha256 TEXT,
            resume_path TEXT,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_event_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            action TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outcome_events_application_date
        ON outcome_events(application, event_date)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outcome_events_active_date
        ON outcome_events(event_date DESC)
        WHERE archived_at IS NULL
        """
    )
    row = connection.execute(
        "SELECT value FROM outcome_schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO outcome_schema_meta(key, value) VALUES('schema_version', ?)",
            (str(OUTCOME_SCHEMA_VERSION),),
        )
    elif int(row[0]) != OUTCOME_SCHEMA_VERSION:
        raise RuntimeError(f"unsupported outcome schema version: {row[0]}")
    connection.execute("PRAGMA optimize")
    connection.commit()


def _event_from_row(row: sqlite3.Row) -> OutcomeEvent:
    return OutcomeEvent(
        application=str(row["application"]),
        status=str(row["status"]),
        date=str(row["event_date"]),
        resume_sha256=str(row["resume_sha256"]) if row["resume_sha256"] else None,
        resume_path=str(row["resume_path"]) if row["resume_path"] else None,
        note=str(row["note"]),
        event_id=str(row["event_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=str(row["archived_at"]) if row["archived_at"] else None,
    )


def _insert_event(connection: sqlite3.Connection, event: OutcomeEvent) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO outcome_events(
            event_id, application, status, event_date, resume_sha256,
            resume_path, note, created_at, updated_at, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.application,
            event.status,
            event.date,
            event.resume_sha256,
            event.resume_path,
            event.note,
            event.created_at,
            event.updated_at,
            event.archived_at,
        ),
    )


def _audit(connection: sqlite3.Connection, action: str, event: OutcomeEvent) -> None:
    connection.execute(
        """
        INSERT INTO outcome_event_audit(event_id, action, snapshot_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (event.event_id, action, json.dumps(asdict(event), ensure_ascii=False), _utc_now()),
    )


def _migrate_legacy_json(connection: sqlite3.Connection, database_path: Path) -> None:
    migrated = connection.execute(
        "SELECT value FROM outcome_schema_meta WHERE key = 'legacy_json_migrated'"
    ).fetchone()
    if migrated is not None:
        return
    source = next((path for path in _legacy_candidates(database_path) if path.is_file()), None)
    if source is None:
        return
    events = _load_json_outcomes(source)
    connection.execute("BEGIN IMMEDIATE")
    try:
        migrated_after_lock = connection.execute(
            "SELECT value FROM outcome_schema_meta WHERE key = 'legacy_json_migrated'"
        ).fetchone()
        if migrated_after_lock is not None:
            connection.rollback()
            return
        for event in events:
            _insert_event(connection, event)
            _audit(connection, "migrate", event)
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        connection.execute(
            "INSERT INTO outcome_schema_meta(key, value) VALUES('legacy_json_migrated', ?)",
            (json.dumps({"path": str(source), "sha256": source_sha256, "at": _utc_now()}),),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        _initialize_schema(connection)
        _migrate_legacy_json(connection, database_path)
        return connection
    except Exception:
        connection.close()
        raise


def load_outcomes(path: Path, *, include_archived: bool = False) -> list[OutcomeEvent]:
    if _is_json_store(path):
        events = _load_json_outcomes(path)
        return events if include_archived else [event for event in events if event.archived_at is None]
    connection = _connect(path)
    try:
        where = "" if include_archived else "WHERE archived_at IS NULL"
        rows = connection.execute(
            f"SELECT * FROM outcome_events {where} ORDER BY event_date, created_at, event_id"
        ).fetchall()
        return [_event_from_row(row) for row in rows]
    finally:
        connection.close()


def save_outcomes(path: Path, events: list[OutcomeEvent]) -> None:
    if not _is_json_store(path):
        raise ValueError("bulk replacement is supported only for legacy JSON stores")
    with _OUTCOME_WRITE_LOCK:
        atomic_write_text(
            path,
            json.dumps([asdict(item) for item in events], ensure_ascii=False, indent=2),
        )


def _backup_directory(database_path: Path) -> Path:
    return database_path.parent / "outcome_backups"


def list_outcome_backups(path: Path) -> list[Path]:
    if _is_json_store(path):
        pattern = "*.json"
    else:
        pattern = "*.sqlite3"
    directory = _backup_directory(path)
    if not directory.exists():
        return []
    return sorted(
        (item for item in directory.glob(pattern) if item.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def create_outcome_backup(path: Path) -> Path:
    if not path.exists():
        if _is_json_store(path):
            save_outcomes(path, [])
        else:
            connection = _connect(path)
            connection.close()
    directory = _backup_directory(path)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = ".json" if _is_json_store(path) else ".sqlite3"
    target = directory / f"application_tracker_{stamp}_{uuid4().hex[:8]}{suffix}"
    try:
        if _is_json_store(path):
            shutil.copy2(path, target)
        else:
            source = sqlite3.connect(path)
            try:
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination)
                    integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise RuntimeError(f"backup integrity check failed: {integrity}")
                finally:
                    destination.close()
            finally:
                source.close()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    backups = list_outcome_backups(path)
    for expired in backups[OUTCOME_BACKUP_LIMIT:]:
        expired.unlink()
    return target


def restore_outcome_backup(path: Path, backup_name: str) -> Path:
    if _is_json_store(path):
        raise ValueError("SQLite storage is required for verified restore")
    backup_root = _backup_directory(path).resolve()
    source = (backup_root / backup_name).resolve()
    if not source.is_relative_to(backup_root) or source.suffix != ".sqlite3" or not source.is_file():
        raise ValueError("invalid outcome backup")
    probe = sqlite3.connect(source)
    try:
        integrity = probe.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"backup integrity check failed: {integrity}")
    finally:
        probe.close()
    with _OUTCOME_WRITE_LOCK:
        source_connection = sqlite3.connect(source)
        try:
            safety_backup = create_outcome_backup(path)
            destination_connection = sqlite3.connect(path)
            try:
                source_connection.backup(destination_connection)
                destination_connection.commit()
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
    return safety_backup


def _backup_after_write(path: Path) -> None:
    if not _is_json_store(path):
        create_outcome_backup(path)


def record_outcome(
    project_root: Path,
    application: str,
    status: str,
    event_date: str | None = None,
    note: str = "",
    path: Path | None = None,
    resume_path: Path | None = None,
    use_default_resume: bool = True,
) -> OutcomeEvent:
    effective_date = _validate_status_and_date(status, event_date)
    resume_sha256, resolved_resume_path = _resume_hash(
        project_root,
        application,
        resume_path,
        use_default_resume=use_default_resume,
    )
    now = _utc_now()
    event = OutcomeEvent(
        application=application,
        status=status,
        date=effective_date,
        resume_sha256=resume_sha256,
        resume_path=resolved_resume_path,
        note=note,
        event_id=str(uuid4()),
        created_at=now,
        updated_at=now,
    )
    outcome_path = path or default_outcome_path(project_root)
    if _is_json_store(outcome_path):
        with _OUTCOME_WRITE_LOCK:
            events = load_outcomes(outcome_path)
            duplicate = next(
                (
                    existing
                    for existing in events
                    if existing.application == event.application
                    and existing.status == event.status
                    and existing.date == event.date
                    and existing.resume_sha256 == event.resume_sha256
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
            events.append(event)
            save_outcomes(outcome_path, events)
        return event
    with _OUTCOME_WRITE_LOCK:
        connection = _connect(outcome_path)
        wrote = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM outcome_events
                WHERE application = ? AND status = ? AND event_date = ?
                  AND resume_sha256 IS ? AND archived_at IS NULL
                LIMIT 1
                """,
                (event.application, event.status, event.date, event.resume_sha256),
            ).fetchone()
            if row is not None:
                connection.rollback()
                return _event_from_row(row)
            _insert_event(connection, event)
            _audit(connection, "create", event)
            connection.commit()
            wrote = True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if wrote:
            _backup_after_write(outcome_path)
    return event


def update_outcome(
    project_root: Path,
    event_id: str,
    application: str,
    status: str,
    event_date: str,
    note: str = "",
    *,
    path: Path | None = None,
    resume_path: Path | None = None,
) -> OutcomeEvent:
    outcome_path = path or default_outcome_path(project_root)
    effective_date = _validate_status_and_date(status, event_date)
    resume_sha256, resolved_resume_path = _resume_hash(
        project_root,
        application,
        resume_path,
        use_default_resume=False,
    )
    if _is_json_store(outcome_path):
        with _OUTCOME_WRITE_LOCK:
            events = load_outcomes(outcome_path)
            index = next((i for i, item in enumerate(events) if item.event_id == event_id), None)
            if index is None:
                raise ValueError("outcome event not found")
            previous = events[index]
            updated = OutcomeEvent(
                application=application,
                status=status,
                date=effective_date,
                resume_sha256=resume_sha256,
                resume_path=resolved_resume_path,
                note=note,
                event_id=event_id,
                created_at=previous.created_at,
                updated_at=_utc_now(),
            )
            events[index] = updated
            save_outcomes(outcome_path, events)
        return updated
    with _OUTCOME_WRITE_LOCK:
        connection = _connect(outcome_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM outcome_events WHERE event_id = ? AND archived_at IS NULL",
                (event_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError("outcome event not found")
            previous = _event_from_row(row)
            updated = OutcomeEvent(
                application=application,
                status=status,
                date=effective_date,
                resume_sha256=resume_sha256,
                resume_path=resolved_resume_path,
                note=note,
                event_id=event_id,
                created_at=previous.created_at,
                updated_at=_utc_now(),
            )
            connection.execute(
                """
                UPDATE outcome_events
                SET application = ?, status = ?, event_date = ?, resume_sha256 = ?,
                    resume_path = ?, note = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (
                    updated.application,
                    updated.status,
                    updated.date,
                    updated.resume_sha256,
                    updated.resume_path,
                    updated.note,
                    updated.updated_at,
                    event_id,
                ),
            )
            _audit(connection, "update", updated)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        _backup_after_write(outcome_path)
    return updated


def delete_outcome(project_root: Path, event_id: str, *, path: Path | None = None) -> None:
    outcome_path = path or default_outcome_path(project_root)
    if _is_json_store(outcome_path):
        with _OUTCOME_WRITE_LOCK:
            events = load_outcomes(outcome_path)
            remaining = [item for item in events if item.event_id != event_id]
            if len(remaining) == len(events):
                raise ValueError("outcome event not found")
            save_outcomes(outcome_path, remaining)
        return
    with _OUTCOME_WRITE_LOCK:
        connection = _connect(outcome_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM outcome_events WHERE event_id = ? AND archived_at IS NULL",
                (event_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError("outcome event not found")
            archived_at = _utc_now()
            connection.execute(
                "UPDATE outcome_events SET archived_at = ?, updated_at = ? WHERE event_id = ?",
                (archived_at, archived_at, event_id),
            )
            archived = OutcomeEvent(
                **{
                    **asdict(_event_from_row(row)),
                    "archived_at": archived_at,
                    "updated_at": archived_at,
                }
            )
            _audit(connection, "archive", archived)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        _backup_after_write(outcome_path)


def restore_outcome(project_root: Path, event_id: str, *, path: Path | None = None) -> OutcomeEvent:
    outcome_path = path or default_outcome_path(project_root)
    if _is_json_store(outcome_path):
        raise ValueError("archived outcome restore requires SQLite storage")
    with _OUTCOME_WRITE_LOCK:
        connection = _connect(outcome_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM outcome_events WHERE event_id = ? AND archived_at IS NOT NULL",
                (event_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError("archived outcome event not found")
            restored_at = _utc_now()
            connection.execute(
                "UPDATE outcome_events SET archived_at = NULL, updated_at = ? WHERE event_id = ?",
                (restored_at, event_id),
            )
            restored = OutcomeEvent(
                **{**asdict(_event_from_row(row)), "archived_at": None, "updated_at": restored_at}
            )
            _audit(connection, "restore", restored)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        _backup_after_write(outcome_path)
    return restored


def outcome_storage_info(project_root: Path) -> dict:
    path = default_outcome_path(project_root)
    mode = _project_setting(project_root, "RESUME_AGENT_DATA_MODE", "preview").strip().lower()
    if mode not in {"preview", "pilot", "trusted"}:
        mode = "preview"
    if _is_json_store(path):
        events = load_outcomes(path, include_archived=True)
        return {
            "mode": mode,
            "backend": "legacy_json",
            "schema_version": None,
            "database_path": str(path.resolve()),
            "backup_directory": str(_backup_directory(path).resolve()),
            "backup_count": len(list_outcome_backups(path)),
            "active_events": len([event for event in events if event.archived_at is None]),
            "archived_events": len([event for event in events if event.archived_at is not None]),
            "integrity": "not_applicable",
        }
    connection = _connect(path)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        version = int(
            connection.execute(
                "SELECT value FROM outcome_schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        )
        active = int(
            connection.execute(
                "SELECT COUNT(*) FROM outcome_events WHERE archived_at IS NULL"
            ).fetchone()[0]
        )
        archived = int(
            connection.execute(
                "SELECT COUNT(*) FROM outcome_events WHERE archived_at IS NOT NULL"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    return {
        "mode": mode,
        "backend": "sqlite",
        "schema_version": version,
        "database_path": str(path.resolve()),
        "backup_directory": str(_backup_directory(path).resolve()),
        "backup_count": len(list_outcome_backups(path)),
        "active_events": active,
        "archived_events": archived,
        "integrity": integrity,
    }


def export_outcomes(events: list[OutcomeEvent], export_format: str) -> tuple[str, str]:
    if export_format == "json":
        return (
            json.dumps([asdict(event) for event in events], ensure_ascii=False, indent=2),
            "application/json; charset=utf-8",
        )
    if export_format != "csv":
        raise ValueError("export format must be json or csv")
    buffer = StringIO()
    fieldnames = [
        "event_id",
        "application",
        "status",
        "date",
        "resume_sha256",
        "resume_path",
        "note",
        "created_at",
        "updated_at",
        "archived_at",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for event in events:
        writer.writerow(asdict(event))
    return buffer.getvalue(), "text/csv; charset=utf-8"


def summarize_outcomes(events: list[OutcomeEvent]) -> dict:
    active_events = [event for event in events if event.archived_at is None]
    ordered = sorted(enumerate(active_events), key=lambda pair: (pair[1].date, pair[0]))
    latest: dict[str, OutcomeEvent] = {}
    ever: dict[str, set[str]] = {status: set() for status in VALID_OUTCOMES}
    for _, event in ordered:
        latest[event.application] = event
        ever[event.status].add(event.application)
    current = {status: 0 for status in VALID_OUTCOMES}
    for event in latest.values():
        current[event.status] += 1
    tracked = len(latest)
    interview_apps = ever["interview"] | ever["offer"]
    return {
        "tracked_applications": tracked,
        "event_count": len(active_events),
        "current_by_status": current,
        "ever_by_status": {status: len(applications) for status, applications in ever.items()},
        "interview_or_offer_count": len(interview_apps),
        "interview_rate": round(len(interview_apps) / tracked, 4) if tracked else 0.0,
        "offer_rate": round(len(ever["offer"]) / tracked, 4) if tracked else 0.0,
    }


def _artifact_state(filename: str) -> str:
    if "废弃" in filename:
        return "discarded"
    if "未验证勿投递" in filename or "草稿勿投递" in filename:
        return "unverified"
    if "已投递旧版" in filename:
        return "delivered_old"
    return "candidate"


def list_resume_artifacts(project_root: Path) -> list[ResumeArtifact]:
    roots = (
        ("output", project_root / "data" / "outputs", "项目输出"),
        ("delivery", default_delivery_root(project_root), "投递版本"),
    )
    artifacts: list[ResumeArtifact] = []
    for source, root, source_label in roots:
        if not root.exists():
            continue
        for pdf_path in sorted(root.rglob("*.pdf")):
            if not pdf_path.is_file():
                continue
            relative = pdf_path.relative_to(root).as_posix()
            application_hint = Path(relative).parts[0]
            modified_at = datetime.fromtimestamp(
                pdf_path.stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds")
            artifacts.append(
                ResumeArtifact(
                    ref=f"{source}:{relative}",
                    label=f"[{source_label}] {relative}",
                    source=source,
                    state=_artifact_state(pdf_path.name),
                    filename=pdf_path.name,
                    application_hint=application_hint,
                    modified_at=modified_at,
                )
            )
    return artifacts


def resolve_resume_ref(project_root: Path, resume_ref: str | None) -> Path | None:
    if not resume_ref:
        return None
    source, separator, relative = resume_ref.partition(":")
    if not separator or source not in {"output", "delivery"} or not relative:
        raise ValueError("invalid resume reference")
    root = (
        project_root / "data" / "outputs"
        if source == "output"
        else default_delivery_root(project_root)
    ).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() != ".pdf":
        raise ValueError("resume reference is outside the allowed PDF roots")
    if not candidate.is_file():
        raise ValueError("resume PDF not found")
    return candidate


def resume_ref_for_path(project_root: Path, resume_path: str | None) -> str | None:
    if not resume_path:
        return None
    candidate = Path(resume_path).resolve()
    roots = (
        ("output", (project_root / "data" / "outputs").resolve()),
        ("delivery", default_delivery_root(project_root).resolve()),
    )
    for source, root in roots:
        if candidate.is_relative_to(root):
            return f"{source}:{candidate.relative_to(root).as_posix()}"
    return None


def render_outcomes(events: list[OutcomeEvent]) -> str:
    if not events:
        return "No application outcomes recorded."
    lines = ["Application outcomes:"]
    for event in sorted(events, key=lambda item: (item.date, item.application)):
        digest = event.resume_sha256[:12] if event.resume_sha256 else "no-pdf"
        note = f" - {event.note}" if event.note else ""
        lines.append(f"- {event.date} {event.application}: {event.status} (resume={digest}){note}")
    return "\n".join(lines)

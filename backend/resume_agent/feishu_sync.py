"""Read-only Feishu Sheets synchronization for the local application dashboard.

Feishu remains the operational application ledger. This module stores a
versioned local snapshot for deterministic analysis; it never writes to the
remote spreadsheet and never invokes an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

import requests

from .feishu_analysis import analyze_feishu_values
from .outcomes import create_outcome_backup, default_outcome_path


FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_SOURCE_ID = "primary"
DEFAULT_CELL_RANGE = "A1:Z500"
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_SNAPSHOT_CELLS = 20_000
TOKEN_REFRESH_SKEW_SECONDS = 600
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CELL_RANGE_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$")


class FeishuSyncError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 503,
        retryable: bool = False,
        provider_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.provider_code = provider_code


@dataclass(frozen=True)
class FeishuConfig:
    document_url: str
    spreadsheet_token: str
    app_id: str
    app_secret: str
    sheet_id: str | None
    cell_range: str
    timeout_seconds: float


@dataclass(frozen=True)
class FeishuSheetSnapshot:
    document_url: str
    sheet_id: str
    sheet_title: str
    revision: str | None
    values: list[list[Any]]
    content_sha256: str
    row_count: int
    column_count: int


@dataclass(frozen=True)
class _TokenCacheEntry:
    credential_fingerprint: str
    token: str
    refresh_at_monotonic: float


_TOKEN_CACHE: _TokenCacheEntry | None = None
_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_REJECTED_CODES = {99991661, 99991663, 99991668, 99991671, 99991677}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _project_setting(project_root: Path, name: str, default: str = "") -> str:
    configured = os.environ.get(name)
    if configured:
        return configured.strip()
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


def _spreadsheet_token(document_url: str) -> str:
    parsed = urlparse(document_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (host != "feishu.cn" and not host.endswith(".feishu.cn")):
        raise FeishuSyncError(
            "INVALID_FEISHU_URL",
            "Feishu spreadsheet URL must use HTTPS on a feishu.cn host.",
            http_status=400,
        )
    parts = [part for part in parsed.path.split("/") if part]
    try:
        token = parts[parts.index("sheets") + 1]
    except (ValueError, IndexError) as exc:
        raise FeishuSyncError(
            "INVALID_FEISHU_URL",
            "Feishu spreadsheet URL does not contain a spreadsheet token.",
            http_status=400,
        ) from exc
    if not _TOKEN_RE.fullmatch(token):
        raise FeishuSyncError(
            "INVALID_FEISHU_TOKEN",
            "Feishu spreadsheet token has an invalid format.",
            http_status=400,
        )
    return token


def load_feishu_config(project_root: Path) -> FeishuConfig:
    document_url = _project_setting(project_root, "RESUME_AGENT_FEISHU_SPREADSHEET_URL")
    app_id = _project_setting(project_root, "RESUME_AGENT_FEISHU_APP_ID")
    app_secret = _project_setting(project_root, "RESUME_AGENT_FEISHU_APP_SECRET")
    missing = [
        name
        for name, value in (
            ("RESUME_AGENT_FEISHU_SPREADSHEET_URL", document_url),
            ("RESUME_AGENT_FEISHU_APP_ID", app_id),
            ("RESUME_AGENT_FEISHU_APP_SECRET", app_secret),
        )
        if not value
    ]
    if missing:
        raise FeishuSyncError(
            "FEISHU_NOT_CONFIGURED",
            f"Missing local Feishu settings: {', '.join(missing)}.",
            http_status=503,
        )
    sheet_id = _project_setting(project_root, "RESUME_AGENT_FEISHU_SHEET_ID") or None
    if sheet_id is not None and not _SHEET_ID_RE.fullmatch(sheet_id):
        raise FeishuSyncError("INVALID_FEISHU_SHEET_ID", "Configured Feishu sheet ID is invalid.", http_status=400)
    cell_range = _project_setting(project_root, "RESUME_AGENT_FEISHU_RANGE", DEFAULT_CELL_RANGE).upper()
    if not _CELL_RANGE_RE.fullmatch(cell_range):
        raise FeishuSyncError(
            "INVALID_FEISHU_RANGE",
            "Feishu range must look like A1:Z500 and must not include a sheet ID.",
            http_status=400,
        )
    try:
        timeout_seconds = float(_project_setting(project_root, "RESUME_AGENT_FEISHU_TIMEOUT_SECONDS", "10"))
    except ValueError as exc:
        raise FeishuSyncError("INVALID_FEISHU_TIMEOUT", "Feishu timeout must be numeric.", http_status=400) from exc
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise FeishuSyncError(
            "INVALID_FEISHU_TIMEOUT",
            "Feishu timeout must be greater than 0 and no more than 60 seconds.",
            http_status=400,
        )
    return FeishuConfig(
        document_url=document_url,
        spreadsheet_token=_spreadsheet_token(document_url),
        app_id=app_id,
        app_secret=app_secret,
        sheet_id=sheet_id,
        cell_range=cell_range,
        timeout_seconds=timeout_seconds,
    )


def _json_response(response: requests.Response, operation: str) -> dict[str, Any]:
    if response.status_code == 401:
        raise FeishuSyncError(
            "FEISHU_TOKEN_REJECTED",
            f"Feishu rejected the access token while performing {operation}.",
            http_status=503,
            retryable=True,
        )
    if response.status_code == 403:
        raise FeishuSyncError(
            "FEISHU_PERMISSION_DENIED",
            f"Feishu denied {operation}; check app permissions and document collaboration access.",
            http_status=503,
        )
    if response.status_code == 429:
        raise FeishuSyncError(
            "FEISHU_RATE_LIMITED",
            f"Feishu rate-limited {operation}.",
            http_status=503,
            retryable=True,
        )
    if response.status_code >= 500:
        raise FeishuSyncError(
            "FEISHU_UPSTREAM_UNAVAILABLE",
            f"Feishu is unavailable while performing {operation}.",
            http_status=503,
            retryable=True,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise FeishuSyncError(
            "FEISHU_INVALID_RESPONSE",
            f"Feishu returned a non-JSON response for {operation}.",
            http_status=502,
            retryable=True,
        ) from exc
    provider_code = payload.get("code")
    if response.status_code >= 400 or provider_code not in (None, 0):
        code = int(provider_code) if isinstance(provider_code, int) else None
        if operation == "authentication" and code in {10014, 10015, 99991543}:
            raise FeishuSyncError(
                "FEISHU_CREDENTIALS_INVALID",
                "Feishu rejected the App ID or App Secret. Copy the current credential pair from the same internal app.",
                http_status=503,
                provider_code=code,
            )
        if code in _TOKEN_REJECTED_CODES:
            raise FeishuSyncError(
                "FEISHU_TOKEN_REJECTED",
                f"Feishu rejected the access token while performing {operation}.",
                http_status=503,
                retryable=True,
                provider_code=code,
            )
        raise FeishuSyncError(
            "FEISHU_API_ERROR",
            f"Feishu rejected {operation}. Check the app permission scope and document access.",
            http_status=503,
            provider_code=code,
        )
    return payload


def _credential_fingerprint(config: FeishuConfig) -> str:
    return hashlib.sha256(f"{config.app_id}\0{config.app_secret}".encode("utf-8")).hexdigest()


def _clear_access_token_cache() -> None:
    global _TOKEN_CACHE
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE = None


def _access_token(config: FeishuConfig, session: Any, *, force_refresh: bool = False) -> str:
    global _TOKEN_CACHE
    fingerprint = _credential_fingerprint(config)
    with _TOKEN_CACHE_LOCK:
        now = time.monotonic()
        if (
            not force_refresh
            and _TOKEN_CACHE is not None
            and _TOKEN_CACHE.credential_fingerprint == fingerprint
            and now < _TOKEN_CACHE.refresh_at_monotonic
        ):
            return _TOKEN_CACHE.token
        response = session.post(
            f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": config.app_id, "app_secret": config.app_secret},
            timeout=config.timeout_seconds,
        )
        payload = _json_response(response, "authentication")
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuSyncError(
                "FEISHU_INVALID_RESPONSE",
                "Feishu authentication response omitted the token.",
                http_status=502,
            )
        expire = payload.get("expire", 7200)
        try:
            lifetime_seconds = max(1.0, float(expire))
        except (TypeError, ValueError):
            lifetime_seconds = 7200.0
        cache_seconds = max(1.0, lifetime_seconds - TOKEN_REFRESH_SKEW_SECONDS)
        _TOKEN_CACHE = _TokenCacheEntry(
            credential_fingerprint=fingerprint,
            token=token,
            refresh_at_monotonic=now + cache_seconds,
        )
        return token


def _select_sheet(config: FeishuConfig, token: str, session: Any) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    response = session.get(
        f"{FEISHU_API_BASE}/sheets/v3/spreadsheets/{config.spreadsheet_token}/sheets/query",
        headers=headers,
        timeout=config.timeout_seconds,
    )
    payload = _json_response(response, "sheet discovery")
    sheets = payload.get("data", {}).get("sheets", [])
    if not isinstance(sheets, list):
        raise FeishuSyncError("FEISHU_INVALID_RESPONSE", "Feishu sheet discovery returned invalid data.", http_status=502)
    candidates = [item for item in sheets if isinstance(item, dict) and item.get("sheet_id")]
    if config.sheet_id:
        selected = next((item for item in candidates if item.get("sheet_id") == config.sheet_id), None)
    else:
        selected = next((item for item in candidates if not item.get("hidden")), candidates[0] if candidates else None)
    if selected is None:
        raise FeishuSyncError("FEISHU_SHEET_NOT_FOUND", "No readable worksheet was found in the Feishu spreadsheet.", http_status=404)
    return str(selected["sheet_id"]), str(selected.get("title") or selected["sheet_id"])


def fetch_feishu_snapshot(project_root: Path, *, session: Any = requests) -> FeishuSheetSnapshot:
    config = load_feishu_config(project_root)
    try:
        token = _access_token(config, session)
        try:
            sheet_id, sheet_title, payload = _read_sheet(config, token, session)
        except FeishuSyncError as exc:
            if exc.code != "FEISHU_TOKEN_REJECTED":
                raise
            token = _access_token(config, session, force_refresh=True)
            sheet_id, sheet_title, payload = _read_sheet(config, token, session)
    except requests.Timeout as exc:
        raise FeishuSyncError(
            "FEISHU_TIMEOUT",
            "Feishu synchronization timed out.",
            http_status=504,
            retryable=True,
        ) from exc
    except requests.RequestException as exc:
        raise FeishuSyncError(
            "FEISHU_NETWORK_ERROR",
            "Could not reach Feishu for synchronization.",
            http_status=503,
            retryable=True,
        ) from exc
    data = payload.get("data", {})
    value_range = data.get("valueRange", {})
    values = value_range.get("values", [])
    if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
        raise FeishuSyncError("FEISHU_INVALID_RESPONSE", "Feishu range response has invalid row data.", http_status=502)
    values = _trim_empty_edges(values)
    cell_count = sum(len(row) for row in values)
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if cell_count > MAX_SNAPSHOT_CELLS or len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise FeishuSyncError(
            "FEISHU_SNAPSHOT_TOO_LARGE",
            "The configured Feishu range is too large for the local preview store.",
            http_status=413,
        )
    revision = value_range.get("revision", data.get("revision"))
    return FeishuSheetSnapshot(
        document_url=config.document_url,
        sheet_id=sheet_id,
        sheet_title=sheet_title,
        revision=str(revision) if revision is not None else None,
        values=values,
        content_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        row_count=len(values),
        column_count=max((len(row) for row in values), default=0),
    )


def _read_sheet(config: FeishuConfig, token: str, session: Any) -> tuple[str, str, dict[str, Any]]:
    sheet_id, sheet_title = _select_sheet(config, token, session)
    requested_range = f"{sheet_id}!{config.cell_range}"
    encoded_range = quote(requested_range, safe="!:$")
    response = session.get(
        f"{FEISHU_API_BASE}/sheets/v2/spreadsheets/{config.spreadsheet_token}/values/{encoded_range}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        timeout=config.timeout_seconds,
    )
    return sheet_id, sheet_title, _json_response(response, "range read")


def _trim_empty_edges(values: list[list[Any]]) -> list[list[Any]]:
    def populated(value: Any) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    trimmed: list[list[Any]] = []
    for row in values:
        end = len(row)
        while end and not populated(row[end - 1]):
            end -= 1
        trimmed.append(row[:end])
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed


def _database_path(project_root: Path) -> Path:
    outcome_path = default_outcome_path(project_root)
    return outcome_path if outcome_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} else project_root / "data" / "feishu_sync.sqlite3"


def _schema_exists(path: Path) -> bool:
    if not path.is_file():
        return False
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='feishu_sync_state'"
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feishu_sync_state (
            source_id TEXT PRIMARY KEY,
            document_url TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_title TEXT NOT NULL,
            source_revision TEXT,
            content_sha256 TEXT NOT NULL,
            last_success_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feishu_sheet_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_revision TEXT,
            content_sha256 TEXT NOT NULL,
            values_json TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            synced_at TEXT NOT NULL,
            UNIQUE(source_id, content_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feishu_snapshots_source_time
        ON feishu_sheet_snapshots(source_id, synced_at DESC)
        """
    )
    connection.execute("PRAGMA optimize")


def persist_feishu_snapshot(project_root: Path, snapshot: FeishuSheetSnapshot) -> dict[str, Any]:
    path = _database_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not _schema_exists(path):
        create_outcome_backup(path)
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        _initialize_schema(connection)
        previous = connection.execute(
            "SELECT content_sha256 FROM feishu_sync_state WHERE source_id = ?",
            (FEISHU_SOURCE_ID,),
        ).fetchone()
        changed = previous is None or previous[0] != snapshot.content_sha256
        synced_at = _utc_now()
        if changed:
            connection.execute(
                """
                INSERT OR IGNORE INTO feishu_sheet_snapshots(
                    snapshot_id, source_id, source_revision, content_sha256,
                    values_json, row_count, column_count, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    FEISHU_SOURCE_ID,
                    snapshot.revision,
                    snapshot.content_sha256,
                    json.dumps(snapshot.values, ensure_ascii=False),
                    snapshot.row_count,
                    snapshot.column_count,
                    synced_at,
                ),
            )
        connection.execute(
            """
            INSERT INTO feishu_sync_state(
                source_id, document_url, sheet_id, sheet_title, source_revision,
                content_sha256, last_success_at, row_count, column_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                document_url=excluded.document_url,
                sheet_id=excluded.sheet_id,
                sheet_title=excluded.sheet_title,
                source_revision=excluded.source_revision,
                content_sha256=excluded.content_sha256,
                last_success_at=excluded.last_success_at,
                row_count=excluded.row_count,
                column_count=excluded.column_count
            """,
            (
                FEISHU_SOURCE_ID,
                snapshot.document_url,
                snapshot.sheet_id,
                snapshot.sheet_title,
                snapshot.revision,
                snapshot.content_sha256,
                synced_at,
                snapshot.row_count,
                snapshot.column_count,
            ),
        )
        connection.commit()
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM feishu_sheet_snapshots WHERE source_id = ?",
            (FEISHU_SOURCE_ID,),
        ).fetchone()[0]
    finally:
        connection.close()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"changed": changed, "snapshot_count": int(snapshot_count), "database_path": str(path.resolve())}


def feishu_sync_status(project_root: Path, *, include_values: bool = True) -> dict[str, Any]:
    try:
        config = load_feishu_config(project_root)
        configured = True
        document_url = config.document_url
        missing_settings: list[str] = []
    except FeishuSyncError as exc:
        configured = False
        document_url = _project_setting(project_root, "RESUME_AGENT_FEISHU_SPREADSHEET_URL") or None
        missing_settings = []
        if exc.code == "FEISHU_NOT_CONFIGURED":
            missing_settings = [part.strip().rstrip(".") for part in str(exc).split(":", 1)[-1].split(",")]
    path = _database_path(project_root)
    if not _schema_exists(path):
        return {
            "configured": configured,
            "connected": False,
            "document_url": document_url,
            "missing_settings": missing_settings,
            "last_success_at": None,
            "source_revision": None,
            "row_count": 0,
            "column_count": 0,
            "snapshot_count": 0,
            "values": [],
            "analysis": None,
        }
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        state = connection.execute(
            "SELECT * FROM feishu_sync_state WHERE source_id = ?",
            (FEISHU_SOURCE_ID,),
        ).fetchone()
        if state is None:
            return {
                "configured": configured,
                "connected": False,
                "document_url": document_url,
                "missing_settings": missing_settings,
                "last_success_at": None,
                "source_revision": None,
                "row_count": 0,
                "column_count": 0,
                "snapshot_count": 0,
                "values": [],
                "analysis": None,
            }
        latest = connection.execute(
            """
            SELECT values_json FROM feishu_sheet_snapshots
            WHERE source_id = ? ORDER BY synced_at DESC LIMIT 1
            """,
            (FEISHU_SOURCE_ID,),
        ).fetchone()
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM feishu_sheet_snapshots WHERE source_id = ?",
            (FEISHU_SOURCE_ID,),
        ).fetchone()[0]
        values = json.loads(latest[0]) if include_values and latest is not None else []
        return {
            "configured": configured,
            "connected": True,
            "document_url": document_url or state["document_url"],
            "missing_settings": missing_settings,
            "sheet_id": state["sheet_id"],
            "sheet_title": state["sheet_title"],
            "last_success_at": state["last_success_at"],
            "source_revision": state["source_revision"],
            "content_sha256": state["content_sha256"],
            "row_count": state["row_count"],
            "column_count": state["column_count"],
            "snapshot_count": int(snapshot_count),
            "values": values,
            "analysis": analyze_feishu_values(values) if include_values else None,
        }
    finally:
        connection.close()


def sync_feishu_sheet(project_root: Path, *, session: Any = requests) -> dict[str, Any]:
    snapshot = fetch_feishu_snapshot(project_root, session=session)
    result = persist_feishu_snapshot(project_root, snapshot)
    return {
        **result,
        "sheet_id": snapshot.sheet_id,
        "sheet_title": snapshot.sheet_title,
        "source_revision": snapshot.revision,
        "row_count": snapshot.row_count,
        "column_count": snapshot.column_count,
        "content_sha256": snapshot.content_sha256,
    }

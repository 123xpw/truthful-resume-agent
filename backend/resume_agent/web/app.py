"""FastAPI Web UI：封装现有 CLI 功能为 REST API + 单页前端。

启动：
    .venv/bin/uvicorn backend.resume_agent.web.app:app --reload
"""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
import os
from pathlib import Path
import sqlite3
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ..agent.observability import log_event
from ..agent.runtime import AgentInvocationError, AgentRuntime, DEFAULT_RUNTIME_DB
from ..analyzer import analyze_jd, save_jd_memory, slugify
from ..fact_store import load_facts
from ..feishu_links import (
    delete_feishu_application_link,
    list_feishu_application_links,
    save_feishu_application_link,
)
from ..feishu_sync import FeishuSyncError, feishu_sync_status, sync_feishu_sheet
from ..gaps import build_gap_report, render_gap_report
from ..gap_trends import load_snapshots
from ..interview_feedback import load_feedback, record_feedback, render_feedback
from ..interview_study import (
    build_study_payload,
    default_progress_path,
    load_study_topics,
    save_study_progress,
)
from ..job_analysis import build_job_analysis_preview
from ..mastery_history import load_mastery_history, render_mastery_history
from ..outcomes import (
    VALID_OUTCOMES,
    create_outcome_backup,
    default_outcome_path,
    delete_outcome,
    export_outcomes,
    list_resume_artifacts,
    list_outcome_backups,
    load_outcomes,
    outcome_storage_info,
    record_outcome,
    resolve_resume_ref,
    restore_outcome,
    restore_outcome_backup,
    resume_ref_for_path,
    summarize_outcomes,
    update_outcome,
)
from ..status import inspect_application, list_applications, render_status, status_stage
from ..llm_client import LLMNotConfigured, get_api_key


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = Path(__file__).parent / "templates"
API_CONTRACT_VERSION = "2026-08-26.2"

app = FastAPI(title="Truthful Resume Agent", version="0.3.0-dev")


class AnalyzeRequest(BaseModel):
    jd_text: str
    name: str
    matcher: Literal["keyword", "semantic"] = "keyword"
    fallback_to_keyword: bool = True


class JobAnalysisPreviewRequest(BaseModel):
    jd_text: str = Field(min_length=1, max_length=100_000)


class AgentMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class InterviewFeedbackRequest(BaseModel):
    application: str
    fact_id: str
    question: str
    note: str = ""
    date: str | None = None


class OutcomeRequest(BaseModel):
    application: str = Field(min_length=1, max_length=120)
    status: str = Field(min_length=1, max_length=32)
    date: str | None = None
    note: str = Field(default="", max_length=1000)
    resume_ref: str | None = Field(default=None, max_length=1000)


class OutcomeBackupRestoreRequest(BaseModel):
    confirm: Literal["RESTORE"]


class FeishuApplicationLinkRequest(BaseModel):
    application: str = Field(min_length=1, max_length=120)
    resume_ref: str | None = Field(default=None, max_length=1000)


class InterviewStudyProgressRequest(BaseModel):
    status: Literal["unfamiliar", "fuzzy", "ready", "mastered"]


@lru_cache(maxsize=1)
def get_agent_runtime() -> AgentRuntime:
    configured = os.environ.get("RESUME_AGENT_RUNTIME_DB")
    return AgentRuntime(Path(configured) if configured else DEFAULT_RUNTIME_DB)


def get_project_root() -> Path:
    return PROJECT_ROOT


def web_semantic_enabled() -> bool:
    """Keep model downloads and cold starts out of interactive HTTP requests."""
    value = os.environ.get("RESUME_AGENT_WEB_SEMANTIC_ENABLED")
    if value is None:
        env_path = PROJECT_ROOT / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, configured = stripped.partition("=")
                if key.strip() == "RESUME_AGENT_WEB_SEMANTIC_ENABLED":
                    value = configured.strip()
                    break
    return (value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": f"INVALID_{label.upper()}"}) from exc


@app.middleware("http")
async def request_metadata(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log_event(
            "http.request.failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    response.headers["X-Request-ID"] = request_id
    log_event(
        "http.request.completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return response


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/job-analysis", response_class=HTMLResponse)
def job_analysis_page() -> str:
    return (TEMPLATES_DIR / "job_analysis.html").read_text(encoding="utf-8")


@app.get("/project-review", response_class=HTMLResponse)
def project_review_page() -> str:
    """Static decision log and interview refresher; no private runtime data."""
    return (TEMPLATES_DIR / "project_review.html").read_text(encoding="utf-8")


@app.get("/interview-study", response_class=HTMLResponse)
def interview_study_page() -> str:
    """Local active-recall UI backed by an ignored private Markdown handbook."""
    return (TEMPLATES_DIR / "interview_study.html").read_text(encoding="utf-8")


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta")
def api_meta() -> dict:
    return {
        "contract_version": API_CONTRACT_VERSION,
        "jd_analysis": {
            "default_matcher": "keyword",
            "semantic_enabled": web_semantic_enabled(),
        },
        "job_analysis_preview": {
            "path": "/api/job-analysis/preview",
            "saves_jd": False,
            "llm_calls": 0,
        },
        "interview_study": {
            "path": "/api/interview-study",
            "progress_path": "/api/interview-study/progress/{card_id}",
            "llm_calls": 0,
        },
    }


@app.get("/readyz")
def readiness(runtime: AgentRuntime = Depends(get_agent_runtime)) -> dict:
    components: dict[str, str] = {"checkpoint": "ready", "facts": "unavailable", "llm": "not_configured"}
    try:
        load_facts()
        components["facts"] = "ready"
    except Exception:
        pass
    try:
        get_api_key()
        components["llm"] = "configured_not_probed"
    except LLMNotConfigured:
        pass
    ready = components["facts"] == "ready" and runtime is not None
    return {
        "status": "ready" if ready else "not_ready",
        "agent_ready": ready and components["llm"] != "not_configured",
        "components": components,
    }


@app.post("/api/v1/conversations", status_code=201)
def create_conversation(runtime: AgentRuntime = Depends(get_agent_runtime)) -> dict:
    conversation_id, created_at = runtime.create_conversation()
    return {"conversation_id": conversation_id, "created_at": created_at}


@app.post("/api/v1/conversations/{conversation_id}/messages")
def post_agent_message(
    conversation_id: str,
    body: AgentMessageRequest,
    request: Request,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> dict:
    canonical_id = _uuid(conversation_id, "conversation_id")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail={"code": "EMPTY_MESSAGE"})
    if not runtime.conversation_exists(canonical_id):
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    try:
        result = runtime.invoke(
            canonical_id,
            body.message,
            request_id=request.state.request_id,
        )
    except AgentInvocationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "trace_id": exc.trace_id,
            },
        ) from exc
    return {
        "conversation_id": result.conversation_id,
        "trace_id": result.trace_id,
        "status": result.status,
        "answer": result.answer,
        "verified": result.verified,
        "degraded": result.degraded,
        "nodes": list(result.nodes),
    }


@app.get("/api/v1/traces/{trace_id}")
def get_agent_trace(trace_id: str, runtime: AgentRuntime = Depends(get_agent_runtime)) -> dict:
    canonical_id = _uuid(trace_id, "trace_id")
    trace = runtime.get_trace(canonical_id)
    if trace is None:
        raise HTTPException(status_code=404, detail={"code": "TRACE_NOT_FOUND"})
    return trace


@app.get("/api/applications")
def get_applications(project_root: Path = Depends(get_project_root)) -> dict:
    return {
        "applications": [
            {"name": status.name, "state": status_stage(status)}
            for status in list_applications(project_root)
        ]
    }


@app.get("/api/interview-study")
def get_interview_study(project_root: Path = Depends(get_project_root)) -> dict:
    try:
        return build_study_payload(project_root)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERVIEW_STUDY_READ_FAILED",
                "message": "Failed to load the local interview study handbook.",
            },
        ) from exc


@app.put("/api/interview-study/progress/{card_id}")
def put_interview_study_progress(
    card_id: str,
    req: InterviewStudyProgressRequest,
    project_root: Path = Depends(get_project_root),
) -> dict:
    topics, _source = load_study_topics(project_root)
    known_card_ids = {card.card_id for topic in topics for card in topic.cards}
    if card_id not in known_card_ids:
        raise HTTPException(
            status_code=404,
            detail={"code": "INTERVIEW_STUDY_CARD_NOT_FOUND", "message": "Study card not found."},
        )
    try:
        progress = save_study_progress(default_progress_path(project_root), card_id, req.status)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERVIEW_STUDY_WRITE_FAILED",
                "message": "Failed to save local interview study progress.",
            },
        ) from exc
    return {"progress": progress, "llm_calls": 0}


def _outcome_payload(event, project_root: Path) -> dict:
    payload = asdict(event)
    payload["resume_ref"] = resume_ref_for_path(project_root, event.resume_path)
    payload["resume_name"] = Path(event.resume_path).name if event.resume_path else None
    payload.pop("resume_path", None)
    return payload


@app.get("/api/outcomes")
def get_outcomes(project_root: Path = Depends(get_project_root)) -> dict:
    all_events = load_outcomes(default_outcome_path(project_root), include_archived=True)
    events = [event for event in all_events if event.archived_at is None]
    archived = [event for event in all_events if event.archived_at is not None]
    ordered = sorted(
        events,
        key=lambda item: (item.date, item.created_at, item.application, item.event_id),
        reverse=True,
    )
    return {
        "events": [_outcome_payload(event, project_root) for event in ordered],
        "archived_events": [_outcome_payload(event, project_root) for event in reversed(archived)],
        "summary": summarize_outcomes(events),
        "storage": outcome_storage_info(project_root),
        "valid_statuses": sorted(VALID_OUTCOMES),
        "llm_calls": 0,
    }


@app.get("/api/feishu-sync")
def get_feishu_sync(project_root: Path = Depends(get_project_root)) -> dict:
    """Return the last successful read-only Feishu snapshot without network I/O."""
    try:
        status = feishu_sync_status(project_root)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "FEISHU_SYNC_STORE_FAILED", "message": "Failed to read the local Feishu snapshot."},
        ) from exc
    return {"sync": status, "llm_calls": 0}


@app.post("/api/feishu-sync")
def post_feishu_sync(project_root: Path = Depends(get_project_root)) -> dict:
    """Pull one authorized Feishu range and persist a versioned local snapshot."""
    try:
        result = sync_feishu_sheet(project_root)
        status = feishu_sync_status(project_root)
    except FeishuSyncError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "provider_code": exc.provider_code,
            },
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "FEISHU_SYNC_STORE_FAILED", "message": "Failed to save the local Feishu snapshot."},
        ) from exc
    return {"result": result, "sync": status, "llm_calls": 0}


@app.get("/api/feishu-links")
def get_feishu_links(project_root: Path = Depends(get_project_root)) -> dict:
    try:
        result = list_feishu_application_links(project_root)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "FEISHU_LINK_STORE_FAILED", "message": "Failed to read local Feishu links."},
        ) from exc
    return {**result, "llm_calls": 0}


@app.put("/api/feishu-links/{sequence}")
def put_feishu_link(
    sequence: str,
    req: FeishuApplicationLinkRequest,
    project_root: Path = Depends(get_project_root),
) -> dict:
    try:
        link = save_feishu_application_link(
            project_root,
            sequence=sequence,
            application_name=req.application,
            resume_ref=req.resume_ref,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_FEISHU_LINK", "message": str(exc)},
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "FEISHU_LINK_WRITE_FAILED", "message": "Failed to save the local Feishu link."},
        ) from exc
    return {"link": link, "llm_calls": 0}


@app.delete("/api/feishu-links/{sequence}")
def remove_feishu_link(sequence: str, project_root: Path = Depends(get_project_root)) -> dict:
    try:
        delete_feishu_application_link(project_root, sequence)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "FEISHU_LINK_NOT_FOUND", "message": str(exc)},
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "FEISHU_LINK_WRITE_FAILED", "message": "Failed to archive the local Feishu link."},
        ) from exc
    return {"archived": True, "sequence": sequence, "llm_calls": 0}


@app.get("/api/outcomes/storage")
def get_outcome_storage(project_root: Path = Depends(get_project_root)) -> dict:
    return {"storage": outcome_storage_info(project_root), "llm_calls": 0}


@app.post("/api/outcomes/backups", status_code=201)
def post_outcome_backup(project_root: Path = Depends(get_project_root)) -> dict:
    try:
        backup = create_outcome_backup(default_outcome_path(project_root))
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "OUTCOME_BACKUP_FAILED", "message": "Failed to back up outcome data."},
        ) from exc
    return {"backup_name": backup.name, "llm_calls": 0}


@app.get("/api/outcomes/backups")
def get_outcome_backups(project_root: Path = Depends(get_project_root)) -> dict:
    path = default_outcome_path(project_root)
    backups = list_outcome_backups(path)
    return {
        "backups": [
            {
                "name": backup.name,
                "size_bytes": backup.stat().st_size,
                "modified_at": backup.stat().st_mtime,
            }
            for backup in backups
        ],
        "llm_calls": 0,
    }


@app.post("/api/outcomes/backups/{backup_name}/restore")
def post_outcome_backup_restore(
    backup_name: str,
    req: OutcomeBackupRestoreRequest,
    project_root: Path = Depends(get_project_root),
) -> dict:
    try:
        safety_backup = restore_outcome_backup(default_outcome_path(project_root), backup_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_OUTCOME_BACKUP", "message": str(exc)},
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "OUTCOME_RESTORE_FAILED", "message": "Failed to restore outcome data."},
        ) from exc
    return {"restored": True, "safety_backup_name": safety_backup.name, "llm_calls": 0}


@app.get("/api/outcomes/export")
def get_outcome_export(
    format: Literal["json", "csv"] = "json",
    project_root: Path = Depends(get_project_root),
) -> Response:
    events = load_outcomes(default_outcome_path(project_root), include_archived=True)
    content, media_type = export_outcomes(events, format)
    filename = f"application_outcomes.{format}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/resume-artifacts")
def get_resume_artifacts(project_root: Path = Depends(get_project_root)) -> dict:
    artifacts = list_resume_artifacts(project_root)
    return {
        "artifacts": [
            {**asdict(item), "application_key": slugify(item.application_hint)}
            for item in artifacts
        ],
        "count": len(artifacts),
        "llm_calls": 0,
    }


@app.post("/api/outcomes", status_code=201)
def post_outcome(req: OutcomeRequest, project_root: Path = Depends(get_project_root)) -> dict:
    try:
        event = record_outcome(
            project_root=project_root,
            application=slugify(req.application),
            status=req.status,
            event_date=req.date,
            note=req.note.strip(),
            resume_path=resolve_resume_ref(project_root, req.resume_ref),
            use_default_resume=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_OUTCOME", "message": str(exc)}) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "OUTCOME_WRITE_FAILED", "message": "Failed to save outcome."},
        ) from exc
    return {"event": _outcome_payload(event, project_root), "llm_calls": 0}


@app.put("/api/outcomes/{event_id}")
def put_outcome(
    event_id: str,
    req: OutcomeRequest,
    project_root: Path = Depends(get_project_root),
) -> dict:
    try:
        event = update_outcome(
            project_root=project_root,
            event_id=event_id,
            application=slugify(req.application),
            status=req.status,
            event_date=req.date or "",
            note=req.note.strip(),
            resume_path=resolve_resume_ref(project_root, req.resume_ref),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "outcome event not found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"code": "OUTCOME_NOT_FOUND" if status_code == 404 else "INVALID_OUTCOME", "message": str(exc)},
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "OUTCOME_WRITE_FAILED", "message": "Failed to update outcome."},
        ) from exc
    return {"event": _outcome_payload(event, project_root), "llm_calls": 0}


@app.delete("/api/outcomes/{event_id}")
def remove_outcome(event_id: str, project_root: Path = Depends(get_project_root)) -> dict:
    try:
        delete_outcome(project_root, event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "OUTCOME_NOT_FOUND", "message": str(exc)},
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "OUTCOME_WRITE_FAILED", "message": "Failed to delete outcome."},
        ) from exc
    return {"deleted": True, "archived": True, "event_id": event_id, "llm_calls": 0}


@app.post("/api/outcomes/{event_id}/restore")
def restore_archived_outcome(
    event_id: str,
    project_root: Path = Depends(get_project_root),
) -> dict:
    try:
        event = restore_outcome(project_root, event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "OUTCOME_NOT_FOUND", "message": str(exc)},
        ) from exc
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "OUTCOME_RESTORE_FAILED", "message": "Failed to restore outcome."},
        ) from exc
    return {"event": _outcome_payload(event, project_root), "llm_calls": 0}


@app.get("/api/status/{name}")
def get_status(name: str) -> dict:
    status = inspect_application(PROJECT_ROOT, slugify(name))
    return {"name": name, "status": render_status(status)}


@app.post("/api/analyze")
def post_analyze(req: AnalyzeRequest) -> dict:
    if not req.jd_text.strip():
        raise HTTPException(status_code=400, detail="JD text is empty")
    jd_path = save_jd_memory(req.jd_text, PROJECT_ROOT / "data" / "jd_library", name_hint=req.name)
    used_matcher = req.matcher
    degraded = False
    warnings: list[dict[str, str]] = []
    if req.matcher == "semantic" and not web_semantic_enabled():
        if not req.fallback_to_keyword:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "WEB_SEMANTIC_NOT_ENABLED",
                    "message": "Semantic Web analysis is disabled until the local model is prepared.",
                },
            )
        result = analyze_jd(req.jd_text, matcher="keyword")
        used_matcher = "keyword"
        degraded = True
        warnings.append(
            {
                "code": "WEB_SEMANTIC_NOT_ENABLED",
                "message": "Used deterministic keyword retrieval; Web semantic analysis is not enabled.",
            }
        )
        log_event(
            "retrieval.degraded",
            requested_matcher="semantic",
            used_matcher="keyword",
            reason="web_semantic_not_enabled",
        )
        return {
            "jd_path": str(jd_path),
            "requested_matcher": req.matcher,
            "used_matcher": used_matcher,
            "degraded": degraded,
            "warnings": warnings,
            "job_type": result.job_type,
            "not_writable": sorted(result.not_writable),
            "strong_matches": [m.fact.id for m in result.strong_matches],
            "weak_matches": [m.fact.id for m in result.weak_matches],
        }
    try:
        result = analyze_jd(req.jd_text, matcher=req.matcher)
    except Exception as exc:
        if req.matcher != "semantic" or not req.fallback_to_keyword:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SEMANTIC_RETRIEVAL_UNAVAILABLE",
                    "message": "Semantic retrieval is unavailable.",
                },
            ) from exc
        result = analyze_jd(req.jd_text, matcher="keyword")
        used_matcher = "keyword"
        degraded = True
        warnings.append(
            {
                "code": "SEMANTIC_RETRIEVAL_UNAVAILABLE",
                "message": "Fell back to deterministic keyword retrieval.",
            }
        )
        log_event(
            "retrieval.degraded",
            requested_matcher="semantic",
            used_matcher="keyword",
            exception_type=type(exc).__name__,
        )
    return {
        "jd_path": str(jd_path),
        "requested_matcher": req.matcher,
        "used_matcher": used_matcher,
        "degraded": degraded,
        "warnings": warnings,
        "job_type": result.job_type,
        "not_writable": sorted(result.not_writable),
        "strong_matches": [m.fact.id for m in result.strong_matches],
        "weak_matches": [m.fact.id for m in result.weak_matches],
    }


@app.post("/api/job-analysis/preview")
def post_job_analysis_preview(req: JobAnalysisPreviewRequest) -> dict:
    """Analyze explicit JD requirements without saving text or calling an LLM."""
    if not req.jd_text.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "EMPTY_JD", "message": "JD text is empty."},
        )
    return build_job_analysis_preview(req.jd_text)


@app.get("/api/career-trends")
def get_career_trends() -> dict:
    jd_dir = PROJECT_ROOT / "data" / "jd_library"
    jd_paths = sorted(jd_dir.glob("*.md"))
    tech_jds: dict[str, set[str]] = {}
    for jd_path in jd_paths:
        try:
            jd_text = jd_path.read_text(encoding="utf-8")
            result = analyze_jd(jd_text, matcher="keyword")
        except Exception:
            continue
        for tech in result.not_writable:
            tech_jds.setdefault(tech, set()).add(jd_path.stem)
    snapshots = load_snapshots(PROJECT_ROOT)
    return {
        "total_jds": len(jd_paths),
        "high": [{"tech": t, "count": len(jds), "jds": sorted(jds)} for t, jds in sorted(tech_jds.items(), key=lambda kv: (-len(kv[1]), kv[0])) if len(jds) >= 2],
        "low": [{"tech": t, "count": len(jds), "jds": sorted(jds)} for t, jds in sorted(tech_jds.items(), key=lambda kv: (-len(kv[1]), kv[0])) if len(jds) == 1],
        "snapshot_count": len(snapshots),
    }


@app.get("/api/mastery-history")
def get_mastery_history(fact_id: str | None = None) -> dict:
    history = load_mastery_history(PROJECT_ROOT)
    return {"rendered": render_mastery_history(history, fact_id=fact_id), "snapshot_count": len(history)}


@app.post("/api/record-interview")
def post_interview_feedback(req: InterviewFeedbackRequest) -> dict:
    try:
        feedback = record_feedback(
            project_root=PROJECT_ROOT,
            application=slugify(req.application),
            fact_id=req.fact_id,
            question=req.question,
            note=req.note,
            event_date=req.date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (OSError, IOError) as exc:
        raise HTTPException(status_code=500, detail=f"failed to write feedback: {exc}")
    return {"recorded": True, "date": feedback.date, "fact_id": feedback.fact_id}


@app.get("/api/interview-feedback/{name}")
def get_interview_feedback(name: str) -> dict:
    items = load_feedback(PROJECT_ROOT, slugify(name))
    return {"rendered": render_feedback(items), "count": len(items)}


@app.get("/api/gaps/{name}")
def get_gaps(name: str) -> dict:
    report = build_gap_report(PROJECT_ROOT, slugify(name))
    return {"rendered": render_gap_report(report)}

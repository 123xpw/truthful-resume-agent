"""FastAPI Web UI：封装现有 CLI 功能为 REST API + 单页前端。

启动：
    .venv/bin/uvicorn backend.resume_agent.web.app:app --reload
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..agent.observability import log_event
from ..agent.runtime import AgentInvocationError, AgentRuntime, DEFAULT_RUNTIME_DB
from ..analyzer import analyze_jd, save_jd_memory, slugify
from ..fact_store import load_facts
from ..gaps import build_gap_report, render_gap_report
from ..gap_trends import load_snapshots
from ..interview_feedback import load_feedback, record_feedback, render_feedback
from ..mastery_history import load_mastery_history, render_mastery_history
from ..status import inspect_application, list_applications, render_status, status_stage
from ..llm_client import LLMNotConfigured, get_api_key


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Truthful Resume Agent", version="0.2.0")


class AnalyzeRequest(BaseModel):
    jd_text: str
    name: str
    matcher: Literal["keyword", "semantic"] = "keyword"
    fallback_to_keyword: bool = True


class AgentMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class InterviewFeedbackRequest(BaseModel):
    application: str
    fact_id: str
    question: str
    note: str = ""
    date: str | None = None


@lru_cache(maxsize=1)
def get_agent_runtime() -> AgentRuntime:
    configured = os.environ.get("RESUME_AGENT_RUNTIME_DB")
    return AgentRuntime(Path(configured) if configured else DEFAULT_RUNTIME_DB)


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


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok"}


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
def get_applications() -> dict:
    return {
        "applications": [
            {"name": status.name, "state": status_stage(status)}
            for status in list_applications(PROJECT_ROOT)
        ]
    }


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

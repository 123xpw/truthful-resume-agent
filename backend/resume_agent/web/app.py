"""FastAPI Web UI：封装现有 CLI 功能为 REST API + 单页前端。

启动：
    .venv/bin/uvicorn backend.resume_agent.web.app:app --reload
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..analyzer import analyze_jd, save_jd_memory, slugify
from ..gaps import build_gap_report, render_gap_report
from ..gap_trends import load_snapshots
from ..interview_feedback import load_feedback, record_feedback, render_feedback
from ..mastery_history import load_mastery_history, render_mastery_history
from ..status import inspect_application, list_applications, render_status, status_stage


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Truthful Resume Agent", version="1.0.0")


class AnalyzeRequest(BaseModel):
    jd_text: str
    name: str
    matcher: str = "keyword"


class InterviewFeedbackRequest(BaseModel):
    application: str
    fact_id: str
    question: str
    note: str = ""
    date: str | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")


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
    try:
        result = analyze_jd(req.jd_text, matcher=req.matcher)
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"missing dependency: {exc.name}")
    return {
        "jd_path": str(jd_path),
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

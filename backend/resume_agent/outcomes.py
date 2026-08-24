from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
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


@dataclass(frozen=True)
class ResumeArtifact:
    ref: str
    label: str
    source: str
    state: str
    filename: str


def default_outcome_path(project_root: Path) -> Path:
    configured = os.environ.get("RESUME_AGENT_OUTCOME_PATH")
    if configured:
        return Path(configured)
    return project_root / "data" / "application_outcomes.json"


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


def load_outcomes(path: Path) -> list[OutcomeEvent]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        OutcomeEvent(
            application=str(item["application"]),
            status=str(item["status"]),
            date=str(item["date"]),
            resume_sha256=str(item["resume_sha256"]) if item.get("resume_sha256") else None,
            resume_path=str(item["resume_path"]) if item.get("resume_path") else None,
            note=str(item.get("note", "")),
            event_id=str(item.get("event_id") or _legacy_event_id(item, index)),
        )
        for index, item in enumerate(raw)
    ]


def save_outcomes(path: Path, events: list[OutcomeEvent]) -> None:
    with _OUTCOME_WRITE_LOCK:
        atomic_write_text(
            path,
            json.dumps([asdict(item) for item in events], ensure_ascii=False, indent=2),
        )


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
    event = OutcomeEvent(
        application=application,
        status=status,
        date=effective_date,
        resume_sha256=resume_sha256,
        resume_path=resolved_resume_path,
        note=note,
        event_id=str(uuid4()),
    )
    outcome_path = path or default_outcome_path(project_root)
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
    with _OUTCOME_WRITE_LOCK:
        events = load_outcomes(outcome_path)
        index = next((i for i, item in enumerate(events) if item.event_id == event_id), None)
        if index is None:
            raise ValueError("outcome event not found")
        effective_date = _validate_status_and_date(status, event_date)
        resume_sha256, resolved_resume_path = _resume_hash(
            project_root,
            application,
            resume_path,
            use_default_resume=False,
        )
        updated = OutcomeEvent(
            application=application,
            status=status,
            date=effective_date,
            resume_sha256=resume_sha256,
            resume_path=resolved_resume_path,
            note=note,
            event_id=event_id,
        )
        events[index] = updated
        save_outcomes(outcome_path, events)
    return updated


def delete_outcome(project_root: Path, event_id: str, *, path: Path | None = None) -> None:
    outcome_path = path or default_outcome_path(project_root)
    with _OUTCOME_WRITE_LOCK:
        events = load_outcomes(outcome_path)
        remaining = [item for item in events if item.event_id != event_id]
        if len(remaining) == len(events):
            raise ValueError("outcome event not found")
        save_outcomes(outcome_path, remaining)


def summarize_outcomes(events: list[OutcomeEvent]) -> dict:
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1].date, pair[0]))
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
        "event_count": len(events),
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
            artifacts.append(
                ResumeArtifact(
                    ref=f"{source}:{relative}",
                    label=f"[{source_label}] {relative}",
                    source=source,
                    state=_artifact_state(pdf_path.name),
                    filename=pdf_path.name,
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

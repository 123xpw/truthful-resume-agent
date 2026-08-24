from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path

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


@dataclass(frozen=True)
class OutcomeEvent:
    application: str
    status: str
    date: str
    resume_sha256: str | None
    resume_path: str | None
    note: str


def default_outcome_path(project_root: Path) -> Path:
    return project_root / "data" / "application_outcomes.json"


def _resume_hash(project_root: Path, application: str, resume_path: Path | None = None) -> tuple[str | None, str | None]:
    path = resume_path or project_root / "data" / "outputs" / application / "resume_draft.pdf"
    resolved = str(path.resolve()) if path.exists() else None
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return digest, resolved


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
        )
        for item in raw
    ]


def record_outcome(
    project_root: Path,
    application: str,
    status: str,
    event_date: str | None = None,
    note: str = "",
    path: Path | None = None,
    resume_path: Path | None = None,
) -> OutcomeEvent:
    if status not in VALID_OUTCOMES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_OUTCOMES))}")
    effective_date = event_date or date.today().isoformat()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise ValueError("event date must use YYYY-MM-DD") from exc
    resume_sha256, resolved_resume_path = _resume_hash(project_root, application, resume_path)
    event = OutcomeEvent(
        application=application,
        status=status,
        date=effective_date,
        resume_sha256=resume_sha256,
        resume_path=resolved_resume_path,
        note=note,
    )
    outcome_path = path or default_outcome_path(project_root)
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
    atomic_write_text(
        outcome_path,
        json.dumps([asdict(item) for item in events], ensure_ascii=False, indent=2),
    )
    return event


def render_outcomes(events: list[OutcomeEvent]) -> str:
    if not events:
        return "No application outcomes recorded."
    lines = ["Application outcomes:"]
    for event in sorted(events, key=lambda item: (item.date, item.application)):
        digest = event.resume_sha256[:12] if event.resume_sha256 else "no-pdf"
        note = f" - {event.note}" if event.note else ""
        lines.append(f"- {event.date} {event.application}: {event.status} (resume={digest}){note}")
    return "\n".join(lines)

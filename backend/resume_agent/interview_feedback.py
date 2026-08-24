"""面试反馈记录：把面试中暴露的边界问题回写到事实库。

反馈先写入 data/outputs/<application>/interview_feedback.json，
再可选通过 --append-boundary 把边界条目回写到 facts.json 对应 fact 的 boundaries 数组。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path

from .fact_store import load_facts
from .io_utils import atomic_write_text


@dataclass(frozen=True)
class InterviewFeedback:
    application: str
    fact_id: str
    question: str
    note: str
    date: str


def feedback_path(project_root: Path, application: str) -> Path:
    return project_root / "data" / "outputs" / application / "interview_feedback.json"


def load_feedback(project_root: Path, application: str) -> list[InterviewFeedback]:
    path = feedback_path(project_root, application)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items: list[InterviewFeedback] = []
    for item in raw:
        try:
            items.append(
                InterviewFeedback(
                    application=str(item["application"]),
                    fact_id=str(item["fact_id"]),
                    question=str(item["question"]),
                    note=str(item.get("note", "")),
                    date=str(item["date"]),
                )
            )
        except (KeyError, TypeError):
            continue
    return items


def record_feedback(
    project_root: Path,
    application: str,
    fact_id: str,
    question: str,
    note: str = "",
    event_date: str | None = None,
) -> InterviewFeedback:
    known_fact_ids = {
        fact.id for fact in load_facts(project_root / "data" / "facts" / "facts.json")
    }
    if fact_id not in known_fact_ids:
        raise ValueError(f"unknown fact_id: {fact_id}")
    effective_date = event_date or date.today().isoformat()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise ValueError("event date must use YYYY-MM-DD") from exc

    feedback = InterviewFeedback(
        application=application,
        fact_id=fact_id,
        question=question,
        note=note,
        date=effective_date,
    )
    path = feedback_path(project_root, application)
    items = load_feedback(project_root, application)
    items.append(feedback)
    atomic_write_text(
        path,
        json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2),
    )
    return feedback


def render_feedback(items: list[InterviewFeedback]) -> str:
    if not items:
        return "No interview feedback recorded."
    lines = ["Interview feedback:"]
    for item in sorted(items, key=lambda i: (i.date, i.fact_id)):
        note = f" - {item.note}" if item.note else ""
        lines.append(f"- {item.date} [{item.fact_id}] {item.question}{note}")
    return "\n".join(lines)


def append_boundary_to_facts(facts_path: Path, fact_id: str, boundary_text: str) -> str:
    """把一条边界回写到 facts.json 指定 fact 的 boundaries 数组。

    返回值：
    - "written"：实际写入了一条新边界
    - "duplicate"：该边界文本已存在，未重复写入
    - "fact_not_found"：facts.json 中没有该 fact_id
    - "file_not_found"：facts.json 文件不存在
    """
    if not facts_path.exists():
        return "file_not_found"
    raw_items: list[dict] = json.loads(facts_path.read_text(encoding="utf-8"))
    for item in raw_items:
        if str(item.get("id")) != fact_id:
            continue
        boundaries: list[str] = list(item.get("boundaries", []))
        if boundary_text in boundaries:
            return "duplicate"
        boundaries.append(boundary_text)
        item["boundaries"] = boundaries
        atomic_write_text(
            facts_path,
            json.dumps(raw_items, ensure_ascii=False, indent=2),
        )
        return "written"
    return "fact_not_found"

"""Private Markdown-backed interview study cards with local progress tracking."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading


VALID_STUDY_STATUSES = ("unseen", "unfamiliar", "fuzzy", "ready", "mastered")
DEFAULT_STUDY_RELATIVE_PATH = Path("docs/handover_resume_interview_study.md")
DEFAULT_PROGRESS_RELATIVE_PATH = Path("data/runtime/interview_study.sqlite3")
_PROGRESS_LOCK = threading.RLock()

EXAMPLE_STUDY_MARKDOWN = """# 面试技术复习示例

<!-- study-group: Agent 核心与编排 -->
## Agent 基础

### Agent 与固定工作流有什么区别？

固定工作流由代码预先规定主要路径；Agent 让模型在受限工具集和预算中动态选择下一步动作。

### Tool Calling 如何发生？

模型输出工具名称与结构化参数，宿主代码校验并执行函数，再把结果返回模型。

<!-- study-group: RAG 与上下文 -->
## RAG 基础

### RAG 能保证事实正确吗？

不能。RAG 提供外部证据，但仍可能漏召、误召或错误引用，需要 fact ID、边界校验和评测。
"""


@dataclass(frozen=True)
class StudyCard:
    card_id: str
    title: str
    body_markdown: str


@dataclass(frozen=True)
class StudyTopic:
    topic_id: str
    title: str
    group: str
    intro_markdown: str
    cards: tuple[StudyCard, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _project_setting(project_root: Path, name: str, default: str) -> str:
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


def _resolve_setting_path(project_root: Path, name: str, default: Path) -> Path:
    configured = Path(_project_setting(project_root, name, str(default)))
    return configured if configured.is_absolute() else project_root / configured


def default_study_path(project_root: Path) -> Path:
    return _resolve_setting_path(
        project_root,
        "RESUME_AGENT_INTERVIEW_STUDY_PATH",
        DEFAULT_STUDY_RELATIVE_PATH,
    )


def default_progress_path(project_root: Path) -> Path:
    return _resolve_setting_path(
        project_root,
        "RESUME_AGENT_INTERVIEW_STUDY_DB",
        DEFAULT_PROGRESS_RELATIVE_PATH,
    )


def _clean_heading(value: str) -> str:
    return re.sub(r"^\d+\.\s*", "", value.strip())


def _stable_id(kind: str, *parts: str) -> str:
    normalized = "\x1f".join(part.strip().casefold() for part in parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


def parse_study_markdown(text: str) -> tuple[StudyTopic, ...]:
    """Parse grouped H2 topics and H3 answer cards without executing HTML."""

    topics: list[StudyTopic] = []
    topic_title: str | None = None
    topic_group = "基础与项目"
    pending_group: str | None = None
    intro_lines: list[str] = []
    card_title: str | None = None
    card_lines: list[str] = []
    cards: list[tuple[str, str]] = []

    def finish_card() -> None:
        nonlocal card_title, card_lines
        if card_title is None:
            return
        body = "\n".join(card_lines).strip()
        if body:
            cards.append((_clean_heading(card_title), body))
        card_title = None
        card_lines = []

    def finish_topic() -> None:
        nonlocal topic_title, intro_lines, cards
        if topic_title is None:
            return
        finish_card()
        clean_topic = _clean_heading(topic_title)
        intro = "\n".join(intro_lines).strip()
        if not cards and intro:
            cards.append(("核心内容", intro))
            intro = ""
        seen_titles: dict[str, int] = {}
        rendered_cards: list[StudyCard] = []
        for title, body in cards:
            occurrence = seen_titles.get(title, 0) + 1
            seen_titles[title] = occurrence
            identity = title if occurrence == 1 else f"{title}#{occurrence}"
            rendered_cards.append(
                StudyCard(
                    card_id=_stable_id("card", clean_topic, identity),
                    title=title,
                    body_markdown=body,
                )
            )
        if rendered_cards:
            topics.append(
                StudyTopic(
                    topic_id=_stable_id("topic", clean_topic),
                    title=clean_topic,
                    group=topic_group,
                    intro_markdown=intro,
                    cards=tuple(rendered_cards),
                )
            )
        topic_title = None
        intro_lines = []
        cards = []

    for raw_line in text.splitlines():
        group_marker = re.match(
            r"^\s*<!--\s*study-group:\s*(.+?)\s*-->\s*$",
            raw_line,
            flags=re.IGNORECASE,
        )
        h2 = re.match(r"^##\s+(.+?)\s*$", raw_line)
        h3 = re.match(r"^###\s+(.+?)\s*$", raw_line)
        if group_marker:
            pending_group = group_marker.group(1).strip()
            continue
        if h2:
            finish_topic()
            if pending_group:
                topic_group = pending_group
                pending_group = None
            topic_title = h2.group(1)
            continue
        if topic_title is None:
            continue
        if h3:
            finish_card()
            card_title = h3.group(1)
            continue
        if card_title is None:
            intro_lines.append(raw_line)
        else:
            card_lines.append(raw_line)
    finish_topic()
    return tuple(topics)


def load_study_topics(project_root: Path) -> tuple[tuple[StudyTopic, ...], dict[str, object]]:
    path = default_study_path(project_root)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        configured = True
        mode = "private"
        filename = path.name
    else:
        text = EXAMPLE_STUDY_MARKDOWN
        configured = False
        mode = "example"
        filename = "built-in-example"
    topics = parse_study_markdown(text)
    return topics, {"configured": configured, "mode": mode, "filename": filename}


def _connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interview_study_progress (
            card_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (
                status IN ('unseen', 'unfamiliar', 'fuzzy', 'ready', 'mastered')
            ),
            review_count INTEGER NOT NULL DEFAULT 0,
            last_reviewed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def load_study_progress(database_path: Path, card_ids: set[str]) -> dict[str, dict[str, object]]:
    if not card_ids:
        return {}
    with _PROGRESS_LOCK, _connect(database_path) as connection:
        placeholders = ",".join("?" for _ in card_ids)
        rows = connection.execute(
            f"""
            SELECT card_id, status, review_count, last_reviewed_at
            FROM interview_study_progress
            WHERE card_id IN ({placeholders})
            """,
            tuple(sorted(card_ids)),
        ).fetchall()
    progress = {
        str(row["card_id"]): {
            "status": str(row["status"]),
            "review_count": int(row["review_count"]),
            "last_reviewed_at": row["last_reviewed_at"],
        }
        for row in rows
    }
    for card_id in card_ids:
        progress.setdefault(
            card_id,
            {"status": "unseen", "review_count": 0, "last_reviewed_at": None},
        )
    return progress


def save_study_progress(database_path: Path, card_id: str, status: str) -> dict[str, object]:
    if status not in VALID_STUDY_STATUSES[1:]:
        raise ValueError("invalid interview study status")
    now = _utc_now()
    with _PROGRESS_LOCK, _connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO interview_study_progress (
                card_id, status, review_count, last_reviewed_at, updated_at
            ) VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(card_id) DO UPDATE SET
                status = excluded.status,
                review_count = interview_study_progress.review_count + 1,
                last_reviewed_at = excluded.last_reviewed_at,
                updated_at = excluded.updated_at
            """,
            (card_id, status, now, now),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT card_id, status, review_count, last_reviewed_at
            FROM interview_study_progress WHERE card_id = ?
            """,
            (card_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("study progress was not persisted")
    return {
        "card_id": str(row["card_id"]),
        "status": str(row["status"]),
        "review_count": int(row["review_count"]),
        "last_reviewed_at": row["last_reviewed_at"],
    }


def build_study_payload(project_root: Path) -> dict[str, object]:
    topics, source = load_study_topics(project_root)
    card_ids = {card.card_id for topic in topics for card in topic.cards}
    progress = load_study_progress(default_progress_path(project_root), card_ids)
    topic_payloads: list[dict[str, object]] = []
    counts = {status: 0 for status in VALID_STUDY_STATUSES}
    total_reviews = 0
    for topic in topics:
        cards: list[dict[str, object]] = []
        for card in topic.cards:
            card_progress = progress[card.card_id]
            counts[str(card_progress["status"])] += 1
            total_reviews += int(card_progress["review_count"])
            cards.append({**asdict(card), "progress": card_progress})
        topic_payloads.append(
            {
                "topic_id": topic.topic_id,
                "title": topic.title,
                "group": topic.group,
                "intro_markdown": topic.intro_markdown,
                "cards": cards,
            }
        )
    return {
        "source": source,
        "topics": topic_payloads,
        "summary": {
            "topic_count": len(topics),
            "group_count": len({topic.group for topic in topics}),
            "card_count": len(card_ids),
            "status_counts": counts,
            "total_reviews": total_reviews,
        },
        "valid_statuses": list(VALID_STUDY_STATUSES[1:]),
        "llm_calls": 0,
    }

"""Mastery 时间线：追踪 fact 从 C→B→A 的进步轨迹。

每次 decide 完成后记录一次 mastery 快照到 data/mastery_history.json。
mastery-history 命令渲染时间线，显示每个 fact 的等级变化。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .review_parser import parse_review_mastery


@dataclass(frozen=True)
class MasterySnapshot:
    date: str
    application: str
    mastery: dict[str, str]


def history_path(project_root: Path) -> Path:
    return project_root / "data" / "mastery_history.json"


def load_mastery_history(project_root: Path) -> list[MasterySnapshot]:
    path = history_path(project_root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    snapshots: list[MasterySnapshot] = []
    for item in raw:
        try:
            snapshots.append(
                MasterySnapshot(
                    date=str(item["date"]),
                    application=str(item["application"]),
                    mastery={str(k): str(v) for k, v in item["mastery"].items()},
                )
            )
        except (KeyError, TypeError):
            continue
    return snapshots


def record_mastery_snapshot(
    project_root: Path,
    application: str,
    review_path: Path,
    timestamp: str | None = None,
) -> MasterySnapshot | None:
    """从 review_sheet 解析当前 mastery，记录一条快照。

    仅记录交互式确认过的 fact（A/B/C/D）。若无确认项则返回 None。
    """
    if not review_path.exists():
        return None
    mastery = parse_review_mastery(review_path, require_interactive_confirmation=True)
    if not mastery:
        return None
    from datetime import datetime, timezone

    snapshot = MasterySnapshot(
        date=timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        application=application,
        mastery=dict(mastery),
    )
    path = history_path(project_root)
    history = load_mastery_history(project_root)
    history.append(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(item) for item in history], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snapshot


def render_mastery_history(history: list[MasterySnapshot], fact_id: str | None = None) -> str:
    if not history:
        return "No mastery history recorded."
    if fact_id:
        return _render_fact_timeline(history, fact_id)
    return _render_all_timeline(history)


def _render_fact_timeline(history: list[MasterySnapshot], fact_id: str) -> str:
    entries = [(snap.date, snap.application, snap.mastery[fact_id]) for snap in history if fact_id in snap.mastery]
    if not entries:
        return f"No mastery record for fact_id: {fact_id}"
    lines = [f"# Mastery timeline for {fact_id}", ""]
    for date, application, level in entries:
        lines.append(f"- {date} [{application}] -> {level}")
    first_level = entries[0][2]
    last_level = entries[-1][2]
    progression = _progression_label(first_level, last_level)
    lines.append(f"\nProgression: {first_level} -> {last_level} ({progression})")
    return "\n".join(lines)


def _render_all_timeline(history: list[MasterySnapshot]) -> str:
    lines = [f"# Mastery history ({len(history)} snapshots)", ""]
    for snap in history:
        lines.append(f"## {snap.date} [{snap.application}]")
        for fact_id, level in sorted(snap.mastery.items()):
            lines.append(f"- {fact_id}: {level}")
        lines.append("")
    return "\n".join(lines)


def _progression_label(first: str, last: str) -> str:
    rank = {"D": 0, "C": 1, "B": 2, "A": 3}
    if first not in rank or last not in rank:
        return "unknown"
    diff = rank[last] - rank[first]
    if diff > 0:
        return f"improved +{diff}"
    if diff < 0:
        return f"regressed {diff}"
    return "stable"

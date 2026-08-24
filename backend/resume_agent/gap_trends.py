"""JD 缺口模式累积：记录每次 career-trends 快照，对比显示新增/已补齐缺口。

快照存于 data/evaluation/gap_snapshots.json，每次 career-trends 运行时追加。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import html

from .io_utils import atomic_write_text


@dataclass(frozen=True)
class GapSnapshot:
    date: str
    total_jds: int
    gaps: dict[str, list[str]]  # tech -> sorted list of JD stems


@dataclass(frozen=True)
class GapDiff:
    added: list[str]   # 新出现的缺口技术
    resolved: list[str]  # 上次有、这次没有的技术（已被 fact 补齐或 JD 移除）


def snapshot_path(project_root: Path) -> Path:
    return project_root / "data" / "evaluation" / "gap_snapshots.json"


def load_snapshots(project_root: Path) -> list[GapSnapshot]:
    path = snapshot_path(project_root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    snapshots: list[GapSnapshot] = []
    for item in raw:
        try:
            snapshots.append(
                GapSnapshot(
                    date=str(item["date"]),
                    total_jds=int(item["total_jds"]),
                    gaps={
                        str(tech): [str(j) for j in jds]
                        for tech, jds in item["gaps"].items()
                    },
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return snapshots


def record_gap_snapshot(
    project_root: Path,
    tech_jds: dict[str, set[str]],
) -> GapSnapshot:
    snapshot = GapSnapshot(
        date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total_jds=len({jd for jds in tech_jds.values() for jd in jds}),
        gaps={tech: sorted(jds) for tech, jds in tech_jds.items()},
    )
    path = snapshot_path(project_root)
    snapshots = load_snapshots(project_root)
    snapshots.append(snapshot)
    atomic_write_text(
        path,
        json.dumps([asdict(s) for s in snapshots], ensure_ascii=False, indent=2),
    )
    return snapshot


def diff_against_last(current: dict[str, set[str]], history: list[GapSnapshot]) -> GapDiff:
    if not history:
        return GapDiff(added=sorted(current), resolved=[])
    previous = history[-1]
    current_tech = set(current)
    previous_tech = set(previous.gaps)
    added = sorted(current_tech - previous_tech)
    resolved = sorted(previous_tech - current_tech)
    return GapDiff(added=added, resolved=resolved)


def render_career_trends_html(
    total_jds: int,
    tech_jds: dict[str, set[str]],
    diff: GapDiff,
) -> str:
    ranked = sorted(tech_jds.items(), key=lambda item: (-len(item[1]), item[0]))
    cards = "".join(
        "<article class='card'><h2>"
        + html.escape(tech)
        + f" <span>{len(jds)} 份 JD</span></h2><p>"
        + html.escape("、".join(sorted(jds)))
        + "</p></article>"
        for tech, jds in ranked
    ) or "<p>当前没有检测到事实库无证据的技术缺口。</p>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>跨 JD 缺口趋势</title><style>body{{margin:0;background:#f5f7fb;color:#182033;font-family:-apple-system,"PingFang SC",sans-serif}}main{{max-width:960px;margin:auto;padding:28px 18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}.card{{background:white;border:1px solid #dce2ec;border-radius:12px;padding:16px}}h1{{margin-bottom:4px}}h2{{font-size:17px}}span{{font-size:12px;color:#9b2c2c}}.meta{{color:#667085;margin-bottom:20px}}</style></head>
<body><main><h1>跨 JD 缺口趋势</h1><p class="meta">共 {total_jds} 份 JD · 新增：{html.escape('、'.join(diff.added) or '无')} · 已补齐/消失：{html.escape('、'.join(diff.resolved) or '无')}</p><div class="grid">{cards}</div></main></body></html>"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResumeFragment:
    fact_id: str
    source_fact_ids: tuple[str, ...]
    section: str
    entry_type: str
    date: str
    title: str
    organization: str
    keywords: str
    bullets: dict[str, list[str]]
    url_text: str = ""
    url: str = ""


@lru_cache(maxsize=32)
def load_fragments(path: Path | None = None) -> dict[str, ResumeFragment]:
    if path is None:
        path = Path(__file__).resolve().parents[2] / "data" / "resume_fragments" / "fragments.json"
    if not path.exists():
        example_path = path.with_name("fragments.example.json")
        if example_path.exists():
            path = example_path

    raw_items: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    fragments: dict[str, ResumeFragment] = {}
    for item in raw_items:
        source_fact_ids = tuple(str(fact_id) for fact_id in item.get("source_fact_ids", [item["fact_id"]]))
        fragment = ResumeFragment(
            fact_id=str(item["fact_id"]),
            source_fact_ids=source_fact_ids,
            section=str(item["section"]),
            entry_type=str(item["entry_type"]),
            date=str(item["date"]),
            title=str(item["title"]),
            organization=str(item.get("organization", "")),
            keywords=str(item.get("keywords", "")),
            bullets={str(level): [str(bullet) for bullet in bullets] for level, bullets in item["bullets"].items()},
            url_text=str(item.get("url_text", "")),
            url=str(item.get("url", "")),
        )
        fragments[fragment.fact_id] = fragment
    return fragments

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .rules import FACTS, Fact


DEFAULT_FACTS_PATH = Path(__file__).resolve().parents[2] / "data" / "facts" / "facts.json"


def resolve_facts_path(facts_path: Path | None = None) -> Path:
    path = facts_path or DEFAULT_FACTS_PATH
    if path.exists():
        return path
    example_path = path.with_name("facts.example.json")
    return example_path if example_path.exists() else path


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    if value is None:
        return ()
    return (str(value),)


def load_facts(facts_path: Path | None = None) -> tuple[Fact, ...]:
    facts_path = resolve_facts_path(facts_path)

    if not facts_path.exists():
        return FACTS

    raw_items = json.loads(facts_path.read_text(encoding="utf-8"))
    facts: list[Fact] = []
    for item in raw_items:
        facts.append(
            Fact(
                id=str(item["id"]),
                title=str(item["title"]),
                keywords=_as_tuple(item.get("keywords")),
                summary=str(item["summary"]),
                boundaries=_as_tuple(item.get("boundaries")),
                risk=str(item["risk"]),
            )
        )
    return tuple(facts)

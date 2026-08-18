from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "id",
    "title",
    "source_file",
    "source_section",
    "fact_type",
    "keywords",
    "summary",
    "boundaries",
    "risk",
}

FACT_TYPES = {"internship", "project", "skill"}
RISK_LEVELS = {"low", "medium", "high"}


@dataclass
class ValidationResult:
    errors: list[str]
    warnings: list[str]


def validate_facts_file(path: Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        return ValidationResult(errors=[f"Facts file not found: {path}"], warnings=[])

    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationResult(errors=[f"Invalid JSON: {exc}"], warnings=[])

    if not isinstance(data, list):
        return ValidationResult(errors=["Facts file must be a JSON array."], warnings=[])

    seen_ids: set[str] = set()
    for index, item in enumerate(data):
        label = f"item[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: must be an object.")
            continue

        missing = sorted(REQUIRED_FIELDS - set(item))
        if missing:
            errors.append(f"{label}: missing fields: {', '.join(missing)}")

        fact_id = str(item.get("id", "")).strip()
        if not fact_id:
            errors.append(f"{label}: id is empty.")
        elif fact_id in seen_ids:
            errors.append(f"{label}: duplicated id: {fact_id}")
        seen_ids.add(fact_id)

        if item.get("fact_type") not in FACT_TYPES:
            errors.append(f"{label}: invalid fact_type: {item.get('fact_type')}")

        if item.get("risk") not in RISK_LEVELS:
            errors.append(f"{label}: invalid risk: {item.get('risk')}")

        for field in ("keywords", "boundaries"):
            if not isinstance(item.get(field), list) or not item.get(field):
                errors.append(f"{label}: {field} must be a non-empty list.")

        if len(str(item.get("summary", "")).strip()) < 20:
            warnings.append(f"{label}: summary looks too short.")

    return ValidationResult(errors=errors, warnings=warnings)

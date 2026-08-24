"""Reusable, candidate-confirmed resume wording authorizations.

An authorization is global across applications but bound to a content hash of
the fact claims/boundaries and resume-visible fragment content. Retrieval-only
fact metadata (for example keywords or source paths) is deliberately excluded,
so AEO tuning cannot force the candidate to re-confirm unchanged wording. Any
factual boundary or resume-visible wording change still requires confirmation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .review_parser import INTERACTIVE_CONFIRMATION, write_review_state


AUTHORIZATION_LABELS = {
    "A": "A 事实与核心版文案准确，授权用于简历",
    "B": "B 事实准确，仅授权保守版用于简历",
    "C": "C 事实基本准确，但暂不授权写入简历",
    "D": "D 事实记录有误，需要回查事实库",
}
AUTHORIZATION_OPTIONS = " / ".join(AUTHORIZATION_LABELS.values())

FACT_ID_RE = re.compile(r"^- fact_id:\s*`([^`]+)`", re.MULTILINE)
DISPLAY_FACT_ID_RE = re.compile(r"^- display_fact_id:\s*`([^`]+)`", re.MULTILINE)
MASTERY_RE = re.compile(r"^- mastery_check:\s*`?([ABCD])\b.*", re.MULTILINE)
MASTERY_LINE_RE = re.compile(r"^- mastery_check:\s*`.*?`", re.MULTILINE)
PENDING_RE = re.compile(r"^- mastery_check:\s*`(?:待确认|降权)`", re.MULTILINE)
CONFIRMED_VIA_RE = re.compile(r"^- confirmed_via:\s*`?([^`\n]+)`?", re.MULTILINE)
CONFIRMED_AT_RE = re.compile(r"^- confirmed_at:\s*`?([^`\n]+)`?", re.MULTILINE)


def authorization_path(project_root: Path) -> Path:
    return project_root / "data" / "resume_authorizations.json"


def _resolve_data_path(project_root: Path, directory: str, private_name: str, example_name: str) -> Path:
    private_path = project_root / "data" / directory / private_name
    return private_path if private_path.exists() else private_path.with_name(example_name)


def _load_records(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fact_authorization_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only candidate-authorized factual claims, not retrieval metadata."""
    return {
        "id": str(item["id"]),
        "summary": str(item.get("summary", "")),
        "boundaries": [str(value) for value in item.get("boundaries", [])],
    }


def _fragment_authorization_payload(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep resume-visible content; ignore selection-only ordering metadata."""
    if item is None:
        return None
    visible_fields = (
        "fact_id",
        "source_fact_ids",
        "section",
        "entry_type",
        "date",
        "title",
        "organization",
        "keywords",
        "bullets",
        "url_text",
        "url",
    )
    return {field: item[field] for field in visible_fields if field in item}


def _authorization_identity(
    project_root: Path,
    fact_ids: list[str],
    display_fact_id: str | None,
) -> tuple[str, str] | None:
    facts_path = _resolve_data_path(project_root, "facts", "facts.json", "facts.example.json")
    fragments_path = _resolve_data_path(
        project_root,
        "resume_fragments",
        "fragments.json",
        "fragments.example.json",
    )
    facts_by_id = {str(item["id"]): item for item in _load_records(facts_path)}
    if any(fact_id not in facts_by_id for fact_id in fact_ids):
        return None

    fragments = _load_records(fragments_path)
    fragment: dict[str, Any] | None = None
    if display_fact_id:
        fragment = next(
            (item for item in fragments if str(item.get("fact_id")) == display_fact_id),
            None,
        )
    if fragment is None:
        fragment = next(
            (
                item
                for item in fragments
                if [str(source) for source in item.get("source_fact_ids", [item["fact_id"]])]
                == fact_ids
            ),
            None,
        )

    authorization_id = (
        display_fact_id
        or (str(fragment["fact_id"]) if fragment is not None else None)
        or (fact_ids[0] if len(fact_ids) == 1 else "+".join(fact_ids))
    )
    payload = {
        "authorization_id": authorization_id,
        "source_fact_ids": fact_ids,
        "facts": [_fact_authorization_payload(facts_by_id[fact_id]) for fact_id in fact_ids],
        "fragment": _fragment_authorization_payload(fragment),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return authorization_id, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_authorizations(project_root: Path) -> dict[str, dict[str, Any]]:
    path = authorization_path(project_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_valid_authorizations(project_root: Path) -> dict[str, dict[str, Any]]:
    """Return only authorizations whose fact/fragment content still matches.

    This is the application-independent authorized inventory. It lets a new
    JD rank every unchanged A/B fragment instead of treating matcher recall as
    an existence gate. C/D records remain visible to callers as blocked items.
    """
    valid: dict[str, dict[str, Any]] = {}
    for authorization_id, record in load_authorizations(project_root).items():
        source_fact_ids = record.get("source_fact_ids")
        if not isinstance(source_fact_ids, list) or not all(
            isinstance(fact_id, str) for fact_id in source_fact_ids
        ):
            continue
        identity = _authorization_identity(project_root, source_fact_ids, authorization_id)
        if identity is None:
            continue
        resolved_id, content_hash = identity
        if (
            resolved_id == authorization_id
            and record.get("content_hash") == content_hash
            and record.get("confirmed_via") == INTERACTIVE_CONFIRMATION
            and record.get("level") in AUTHORIZATION_LABELS
        ):
            valid[authorization_id] = record
    return valid


def _write_authorizations(project_root: Path, authorizations: dict[str, dict[str, Any]]) -> Path:
    path = authorization_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(authorizations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def _sections(text: str) -> tuple[list[int], list[str]]:
    starts = [match.start() for match in re.finditer(r"^(?:## |### )", text, re.MULTILINE)]
    starts.append(len(text))
    prefix = text[: starts[0]] if starts and starts[0] > 0 else ""
    return starts, [prefix]


def _replace_field(section: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^- {re.escape(field)}:\s*.*$", re.MULTILINE)
    replacement = f"- {field}: {value}"
    if pattern.search(section):
        return pattern.sub(lambda _: replacement, section, count=1)
    anchor = re.search(r"^- allowed_options:.*$", section, re.MULTILINE)
    if anchor:
        return section[: anchor.end()] + "\n" + replacement + section[anchor.end() :]
    return section.rstrip() + "\n" + replacement + "\n"


def record_authorizations_from_review(project_root: Path, review_path: Path) -> int:
    """Persist all interactively confirmed decisions in a review sheet."""
    text = review_path.read_text(encoding="utf-8")
    starts, _ = _sections(text)
    authorizations = load_authorizations(project_root)
    recorded = 0

    for index in range(len(starts) - 1):
        section = text[starts[index] : starts[index + 1]]
        fact_ids = FACT_ID_RE.findall(section)
        mastery_match = MASTERY_RE.search(section)
        via_match = CONFIRMED_VIA_RE.search(section)
        if (
            not fact_ids
            or mastery_match is None
            or via_match is None
            or via_match.group(1).strip() != INTERACTIVE_CONFIRMATION
        ):
            continue
        display_match = DISPLAY_FACT_ID_RE.search(section)
        identity = _authorization_identity(
            project_root,
            fact_ids,
            display_match.group(1) if display_match else None,
        )
        if identity is None:
            continue
        authorization_id, content_hash = identity
        confirmed_at_match = CONFIRMED_AT_RE.search(section)
        confirmed_at = (
            confirmed_at_match.group(1).strip()
            if confirmed_at_match
            else datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        authorizations[authorization_id] = {
            "level": mastery_match.group(1),
            "content_hash": content_hash,
            "source_fact_ids": fact_ids,
            "confirmed_via": INTERACTIVE_CONFIRMATION,
            "confirmed_at": confirmed_at,
        }
        recorded += 1

    if recorded:
        _write_authorizations(project_root, authorizations)
    return recorded


def apply_reusable_authorizations(project_root: Path, review_path: Path) -> int:
    """Apply matching global authorizations to pending items in a new review."""
    authorizations = load_authorizations(project_root)
    if not authorizations:
        return 0

    text = review_path.read_text(encoding="utf-8")
    starts, rebuilt = _sections(text)
    reused = 0

    for index in range(len(starts) - 1):
        section = text[starts[index] : starts[index + 1]]
        fact_ids = FACT_ID_RE.findall(section)
        if not fact_ids or not PENDING_RE.search(section):
            rebuilt.append(section)
            continue
        display_match = DISPLAY_FACT_ID_RE.search(section)
        identity = _authorization_identity(
            project_root,
            fact_ids,
            display_match.group(1) if display_match else None,
        )
        if identity is None:
            rebuilt.append(section)
            continue
        authorization_id, content_hash = identity
        authorization = authorizations.get(authorization_id, {})
        level = str(authorization.get("level", ""))
        if (
            level not in AUTHORIZATION_LABELS
            or authorization.get("content_hash") != content_hash
            or authorization.get("source_fact_ids") != fact_ids
            or authorization.get("confirmed_via") != INTERACTIVE_CONFIRMATION
        ):
            rebuilt.append(section)
            continue

        section = MASTERY_LINE_RE.sub(
            f"- mastery_check: `{AUTHORIZATION_LABELS[level]}`",
            section,
            count=1,
        )
        intensity = "strong" if level == "A" else "conservative" if level == "B" else "blocked"
        section = _replace_field(section, "allowed_resume_intensity", intensity)
        section = _replace_field(section, "confirmed_via", f"`{INTERACTIVE_CONFIRMATION}`")
        section = _replace_field(section, "confirmed_at", f"`{authorization['confirmed_at']}`")
        section = _replace_field(section, "authorization_scope", "`global_content_hash_reuse`")
        section = _replace_field(section, "authorization_id", f"`{authorization_id}`")
        rebuilt.append(section.rstrip() + "\n\n")
        reused += 1

    if reused:
        review_path.write_text("".join(rebuilt).lstrip("\n"), encoding="utf-8")
        write_review_state(review_path)
    return reused

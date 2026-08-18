from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


MASTERY_RE = re.compile(r"^- mastery_check:\s*`?([ABCD])\b.*", re.MULTILINE)
FACT_RE = re.compile(r"^- fact_id:\s*`([^`]+)`", re.MULTILINE)
CONFIRMED_VIA_RE = re.compile(r"^- confirmed_via:\s*`?([^`\n]+)`?", re.MULTILINE)
PENDING_RE = re.compile(r"^- mastery_check:\s*`(?:待确认|降权)`", re.MULTILINE)
INTERACTIVE_CONFIRMATION = "interactive_cli"


@dataclass(frozen=True)
class ReviewDecision:
    fact_id: str
    mastery: str
    confirmed_via: str | None

    @property
    def is_interactively_confirmed(self) -> bool:
        return self.confirmed_via == INTERACTIVE_CONFIRMATION


def _state_path(review_path: Path) -> Path:
    return review_path.with_suffix(".state.json")


def _parse_from_markdown(text: str) -> dict[str, ReviewDecision]:
    sections = re.split(r"\n### ", "\n" + text)
    decisions: dict[str, ReviewDecision] = {}
    for section in sections:
        fact_matches = FACT_RE.findall(section)
        mastery_match = MASTERY_RE.search(section)
        if fact_matches and mastery_match:
            via_match = CONFIRMED_VIA_RE.search(section)
            for fact_id in fact_matches:
                decisions[fact_id] = ReviewDecision(
                    fact_id=fact_id,
                    mastery=mastery_match.group(1),
                    confirmed_via=via_match.group(1).strip() if via_match else None,
                )
    return decisions


def parse_review_decisions(review_path: Path) -> dict[str, ReviewDecision]:
    state_path = _state_path(review_path)
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            return {
                fact_id: ReviewDecision(
                    fact_id=fact_id,
                    mastery=item["mastery"],
                    confirmed_via=item.get("confirmed_via"),
                )
                for fact_id, item in raw.items()
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            # Fall through to markdown parsing on a corrupt sidecar.
            pass
    return _parse_from_markdown(review_path.read_text(encoding="utf-8"))


def write_review_state(review_path: Path) -> Path:
    """Persist decisions from review_sheet.md into a JSON sidecar.

    The markdown stays human-editable; the JSON sidecar is the machine-readable
    source that downstream steps read, so markdown format drift cannot corrupt
    decision parsing.
    """
    decisions = _parse_from_markdown(review_path.read_text(encoding="utf-8"))
    state = {
        fact_id: {
            "mastery": decision.mastery,
            "confirmed_via": decision.confirmed_via,
        }
        for fact_id, decision in decisions.items()
    }
    state_path = _state_path(review_path)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state_path


def parse_review_mastery(review_path: Path, require_interactive_confirmation: bool = True) -> dict[str, str]:
    mastery: dict[str, str] = {}
    for fact_id, decision in parse_review_decisions(review_path).items():
        if require_interactive_confirmation and not decision.is_interactively_confirmed:
            continue
        mastery[fact_id] = decision.mastery
    return mastery


def count_pending_review_items(review_path: Path) -> int:
    if not review_path.exists():
        return 0
    return len(PENDING_RE.findall(review_path.read_text(encoding="utf-8")))


def count_unverified_ab_items(review_path: Path) -> int:
    if not review_path.exists():
        return 0
    return sum(
        1
        for decision in parse_review_decisions(review_path).values()
        if decision.mastery in {"A", "B"} and not decision.is_interactively_confirmed
    )

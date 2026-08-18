from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class EducationProfile:
    date: str
    school: str
    major: str
    details: str


@dataclass(frozen=True)
class SkillProfile:
    text: str
    source_fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResumeProfile:
    name: str
    birth: str
    phone: str
    email: str
    photo_source: str
    education: EducationProfile
    awards: tuple[str, ...]
    skills: tuple[SkillProfile, ...]
    confirmation: str
    source_path: Path


def load_profile(project_root: Path) -> ResumeProfile:
    profile_dir = project_root / "data" / "profile"
    private_path = profile_dir / "profile.private.json"
    example_path = profile_dir / "profile.example.json"
    path = private_path if private_path.exists() else example_path
    if not path.exists():
        raise FileNotFoundError(
            f"Resume profile not found. Create {private_path} from {example_path}."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    education = raw.get("education", {})
    required = {
        "name": raw.get("name"),
        "birth": raw.get("birth"),
        "phone": raw.get("phone"),
        "email": raw.get("email"),
        "education.date": education.get("date"),
        "education.school": education.get("school"),
        "education.major": education.get("major"),
        "education.details": education.get("details"),
    }
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"Resume profile is missing required fields: {', '.join(missing)}")

    skills: list[SkillProfile] = []
    for item in raw.get("skills", []):
        if isinstance(item, str):
            skills.append(SkillProfile(text=item, source_fact_ids=()))
            continue
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            raise ValueError("profile skills must be strings or objects with a non-empty text field")
        source_fact_ids = item.get("source_fact_ids", [])
        if not isinstance(source_fact_ids, list) or not all(
            isinstance(fact_id, str) and fact_id.strip() for fact_id in source_fact_ids
        ):
            raise ValueError("profile skill source_fact_ids must be a list of non-empty strings")
        skills.append(
            SkillProfile(
                text=str(item["text"]),
                source_fact_ids=tuple(source_fact_ids),
            )
        )

    return ResumeProfile(
        name=str(raw["name"]),
        birth=str(raw["birth"]),
        phone=str(raw["phone"]),
        email=str(raw["email"]),
        photo_source=str(raw.get("photo_source", "")),
        education=EducationProfile(
            date=str(education["date"]),
            school=str(education["school"]),
            major=str(education["major"]),
            details=str(education["details"]),
        ),
        awards=tuple(str(item) for item in raw.get("awards", [])),
        skills=tuple(skills),
        confirmation=str(raw.get("confirmation", "unrecorded")),
        source_path=path,
    )

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
class ResumeProfile:
    name: str
    birth: str
    phone: str
    email: str
    photo_source: str
    education: EducationProfile
    awards: tuple[str, ...]
    skills: tuple[str, ...]
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
        skills=tuple(str(item) for item in raw.get("skills", [])),
        confirmation=str(raw.get("confirmation", "unrecorded")),
        source_path=path,
    )

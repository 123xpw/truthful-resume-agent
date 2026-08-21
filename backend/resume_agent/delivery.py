from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

from .quality import ResumeQuality, check_resume_quality


DEFAULT_CANDIDATE = "候选人"
DEFAULT_SCHOOL = "学校"
DEFAULT_MAJOR = "专业"

_UNSAFE_FILENAME_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _sanitize_filename_component(value: str) -> str:
    """清理文件名中的非法字符，防止路径穿越和非法文件名。"""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", value).strip().strip(".")
    return cleaned or "unknown"


@dataclass(frozen=True)
class DeliveryResult:
    company_dir: Path
    pdf_path: Path
    tex_path: Path | None
    quality: ResumeQuality


class DeliveryQualityError(RuntimeError):
    def __init__(self, quality: ResumeQuality) -> None:
        self.quality = quality
        super().__init__("resume export gate failed")


def default_delivery_root(project_root: Path) -> Path:
    return project_root.parent.parent / "投递版本"


def delivery_filenames(
    company: str,
    role: str,
    candidate: str = DEFAULT_CANDIDATE,
    school: str = DEFAULT_SCHOOL,
    major: str = DEFAULT_MAJOR,
) -> tuple[str, str]:
    safe_company = _sanitize_filename_component(company)
    safe_role = _sanitize_filename_component(role)
    safe_candidate = _sanitize_filename_component(candidate)
    safe_school = _sanitize_filename_component(school)
    safe_major = _sanitize_filename_component(major)
    prefix = f"{safe_company}_{safe_role}" if safe_role else safe_company
    pdf_name = f"{prefix}-{safe_school}-{safe_major}-{safe_candidate}.pdf"
    tex_name = f"{prefix}.tex"
    return pdf_name, tex_name


def deliver_resume(
    project_root: Path,
    name: str,
    company: str,
    role: str,
    delivery_root: Path | None = None,
    candidate: str = DEFAULT_CANDIDATE,
    school: str = DEFAULT_SCHOOL,
    major: str = DEFAULT_MAJOR,
    include_tex: bool = True,
) -> DeliveryResult:
    output_dir = project_root / "data" / "outputs" / name
    review_source = output_dir / "review_sheet.md"
    pdf_source = output_dir / "resume_draft.pdf"
    tex_source = output_dir / "resume_draft.tex"
    if not review_source.exists():
        raise FileNotFoundError(f"Review sheet not found: {review_source}")
    if not pdf_source.exists():
        raise FileNotFoundError(f"Resume PDF not found: {pdf_source}")
    if include_tex and not tex_source.exists():
        raise FileNotFoundError(f"Resume TeX not found: {tex_source}")

    quality = check_resume_quality(review_path=review_source, tex_path=tex_source, pdf_path=pdf_source)
    if not quality.passed:
        raise DeliveryQualityError(quality)

    target_root = delivery_root or default_delivery_root(project_root)
    safe_company = _sanitize_filename_component(company)
    company_dir = target_root / safe_company
    company_dir.mkdir(parents=True, exist_ok=True)

    pdf_name, tex_name = delivery_filenames(
        company=company,
        role=role,
        candidate=candidate,
        school=school,
        major=major,
    )
    pdf_target = company_dir / pdf_name
    shutil.copy2(pdf_source, pdf_target)

    tex_target = None
    if include_tex:
        tex_target = company_dir / tex_name
        shutil.copy2(tex_source, tex_target)

    return DeliveryResult(company_dir=company_dir, pdf_path=pdf_target, tex_path=tex_target, quality=quality)

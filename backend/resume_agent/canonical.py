"""Audit and register the actual hand-edited resume used for delivery.

Pipeline drafts and hand-written canonical TeX files are intentionally treated
as different artifacts. A canonical file is registerable only when every
professional bullet has a fact source, either through an exact authorized
fragment match or through an explicit candidate-confirmed provenance mapping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

from .authorization_store import load_valid_authorizations
from .fact_store import load_facts
from .fragments import load_fragments


SECTION_RE = re.compile(r"\\section\{([^}]+)\}")
ITEM_RE = re.compile(
    r"^\s*\\item\s+(.*?)(?=^\s*\\item\b|^\s*\\end\{resumeItems\})",
    re.MULTILINE | re.DOTALL,
)
PROFESSIONAL_SECTIONS = {"实习经历", "项目经历"}


@dataclass(frozen=True)
class CanonicalBulletAudit:
    index: int
    section: str
    text: str
    text_sha256: str
    source_fact_ids: tuple[str, ...]
    match_method: str
    status: str
    reason: str


@dataclass(frozen=True)
class CanonicalAudit:
    application: str
    tex_path: str
    pdf_path: str
    tex_sha256: str | None
    pdf_sha256: str | None
    bullets: tuple[CanonicalBulletAudit, ...]
    ready: bool
    reasons: tuple[str, ...]


def _sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _normalize_bullet(text: str) -> str:
    value = re.sub(r"%.*$", "", text, flags=re.MULTILINE)
    value = re.sub(r"\\textbf\{([^{}]*)\}", r"\1", value)
    value = value.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&")
    value = value.replace("~", " ")
    return re.sub(r"\s+", " ", value).strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_bullet(text).encode("utf-8")).hexdigest()


def extract_professional_bullets(tex: str) -> list[tuple[str, str]]:
    matches = list(SECTION_RE.finditer(tex))
    bullets: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        section = match.group(1)
        if section not in PROFESSIONAL_SECTIONS:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(tex)
        for item in ITEM_RE.finditer(tex[start:end]):
            bullets.append((section, _normalize_bullet(item.group(1))))
    return bullets


def _fragment_bullet_index(project_root: Path) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    fragments = load_fragments(project_root / "data" / "resume_fragments" / "fragments.json")
    index: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for fragment_id, fragment in fragments.items():
        for level in ("A", "B"):
            for bullet in fragment.bullets[level]:
                index.setdefault(_text_hash(bullet), []).append((fragment_id, fragment.source_fact_ids))
    return index


def load_provenance_mapping(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("bullets", []) if isinstance(raw, dict) else []
    if not isinstance(items, list):
        raise ValueError("canonical provenance must contain a bullets array")
    mapping: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text_sha256"), str):
            continue
        mapping[item["text_sha256"]] = item
    return mapping


def audit_canonical_resume(
    project_root: Path,
    application: str,
    tex_path: Path,
    pdf_path: Path,
    provenance_path: Path | None = None,
) -> CanonicalAudit:
    reasons: list[str] = []
    if not tex_path.exists():
        reasons.append(f"canonical tex missing: {tex_path}")
    if not pdf_path.exists():
        reasons.append(f"canonical pdf missing: {pdf_path}")
    if reasons:
        return CanonicalAudit(
            application,
            str(tex_path.resolve()),
            str(pdf_path.resolve()),
            _sha256_file(tex_path),
            _sha256_file(pdf_path),
            (),
            False,
            tuple(reasons),
        )

    facts = {fact.id: fact for fact in load_facts(project_root / "data" / "facts" / "facts.json")}
    valid_authorizations = load_valid_authorizations(project_root)
    fragment_index = _fragment_bullet_index(project_root)
    provenance = load_provenance_mapping(provenance_path)
    extracted = extract_professional_bullets(tex_path.read_text(encoding="utf-8"))
    if not extracted:
        reasons.append("no professional bullets found in canonical tex")

    audits: list[CanonicalBulletAudit] = []
    for index, (section, text) in enumerate(extracted, start=1):
        digest = _text_hash(text)
        exact_matches = fragment_index.get(digest, [])
        authorized_exact = next(
            (
                (fragment_id, source_ids)
                for fragment_id, source_ids in exact_matches
                if valid_authorizations.get(fragment_id, {}).get("level") in {"A", "B"}
            ),
            None,
        )
        if authorized_exact is not None:
            fragment_id, source_ids = authorized_exact
            audit = CanonicalBulletAudit(
                index,
                section,
                text,
                digest,
                source_ids,
                "authorized_fragment_exact",
                "supported",
                f"exact match to current authorized fragment {fragment_id}",
            )
        else:
            mapped = provenance.get(digest, {})
            source_ids = tuple(str(item) for item in mapped.get("source_fact_ids", []))
            unknown = sorted(set(source_ids) - set(facts))
            candidate_confirmed = mapped.get("candidate_confirmed") is True
            if source_ids and not unknown and candidate_confirmed:
                audit = CanonicalBulletAudit(
                    index,
                    section,
                    text,
                    digest,
                    source_ids,
                    "candidate_confirmed_manual_mapping",
                    "supported",
                    "manual wording mapped to existing facts; semantic boundary remains candidate-confirmed",
                )
            else:
                if exact_matches:
                    reason = "exact fragment wording exists, but its current content-hash authorization is stale or blocked"
                elif unknown:
                    reason = "provenance references unknown facts: " + ", ".join(unknown)
                elif source_ids and not candidate_confirmed:
                    reason = "manual provenance exists but candidate_confirmed is not true"
                else:
                    reason = "no exact authorized fragment or candidate-confirmed manual provenance"
                audit = CanonicalBulletAudit(
                    index,
                    section,
                    text,
                    digest,
                    source_ids,
                    "untraced",
                    "blocked",
                    reason,
                )
                reasons.append(f"bullet {index}: {reason}")
        audits.append(audit)

    return CanonicalAudit(
        application=application,
        tex_path=str(tex_path.resolve()),
        pdf_path=str(pdf_path.resolve()),
        tex_sha256=_sha256_file(tex_path),
        pdf_sha256=_sha256_file(pdf_path),
        bullets=tuple(audits),
        ready=not reasons,
        reasons=tuple(reasons),
    )


def render_canonical_audit(audit: CanonicalAudit) -> str:
    lines = [
        f"# Canonical Resume Audit: {audit.application}",
        "",
        f"- status: `{'ready' if audit.ready else 'blocked'}`",
        f"- tex: `{audit.tex_path}`",
        f"- tex_sha256: `{audit.tex_sha256 or 'missing'}`",
        f"- pdf: `{audit.pdf_path}`",
        f"- pdf_sha256: `{audit.pdf_sha256 or 'missing'}`",
        "",
        "## Professional Bullet Provenance",
        "",
    ]
    for bullet in audit.bullets:
        lines.extend(
            [
                f"### {bullet.index}. [{bullet.section}] {bullet.status}",
                "",
                f"- text_sha256: `{bullet.text_sha256}`",
                f"- source_fact_ids: {', '.join(bullet.source_fact_ids) or 'None'}",
                f"- match_method: `{bullet.match_method}`",
                f"- reason: {bullet.reason}",
                f"- text: {bullet.text}",
                "",
            ]
        )
    if audit.reasons:
        lines.extend(["## Blocking Reasons", ""])
        lines.extend(f"- {reason}" for reason in audit.reasons)
        lines.append("")
    return "\n".join(lines)


def write_canonical_audit(
    audit: CanonicalAudit,
    output_dir: Path,
) -> tuple[Path, Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "canonical_audit.md"
    report_path.write_text(render_canonical_audit(audit), encoding="utf-8")
    audit_path = output_dir / "canonical_audit.json"
    audit_path.write_text(json.dumps(asdict(audit), ensure_ascii=False, indent=2), encoding="utf-8")

    todo_path: Path | None = None
    blocked = [bullet for bullet in audit.bullets if bullet.status != "supported"]
    if blocked:
        todo_path = output_dir / "canonical_provenance.todo.json"
        todo_path.write_text(
            json.dumps(
                {
                    "candidate_confirmation_note": (
                        "Fill source_fact_ids only after checking the complete bullet against fact summaries and boundaries; "
                        "then set candidate_confirmed to true."
                    ),
                    "bullets": [
                        {
                            "text_sha256": bullet.text_sha256,
                            "text": bullet.text,
                            "source_fact_ids": list(bullet.source_fact_ids),
                            "candidate_confirmed": False,
                        }
                        for bullet in blocked
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return report_path, audit_path, todo_path


def register_canonical(audit: CanonicalAudit, output_dir: Path) -> Path:
    if not audit.ready:
        raise ValueError("canonical resume audit is blocked")
    manifest_path = output_dir / "canonical_delivery.json"
    manifest_path.write_text(json.dumps(asdict(audit), ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path

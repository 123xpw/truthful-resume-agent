"""Resume layout configuration.

Keeps display ordering, composite-fragment sources, and section limits out of
the generator/selection logic so that adding a new fact or fragment does not
require editing those modules.

New fragments not listed in the hardcoded orders below are automatically
appended at the end (sorted by display_priority then fact_id). To control
a fragment's position, set ``display_priority`` in its JSON metadata
(lower = earlier); unset fragments fall back to the hardcoded order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .fragments import ResumeFragment

# Display order for internships in the resume body.
INTERNSHIP_ORDER = (
    "intern_optimization_combined",
    "intern_optimization_ai_coding",
    "intern_solver_integration_clarabel",
    "intern_data_automation",
    "intern_csharp_ai_mvp",
)

# Composite fragments and the source facts they stand in for.
COMPOSITE_INTERNSHIP_SOURCES = {
    "intern_optimization_combined": {
        "intern_optimization_ai_coding",
        "intern_solver_integration_clarabel",
    },
}

# Mutually exclusive representations of the same internship stint. The first
# selected item is the preferred resume entry; the remaining fact fragments
# stay available for matching/audit but must not consume additional layout rows.
INTERNSHIP_EXCLUSIVE_GROUPS = (
    (
        "intern_optimization_combined",
        "intern_optimization_ai_coding",
        "intern_solver_integration_clarabel",
        "intern_scip_heuristic_analysis",
    ),
)

# Display order for projects, keyed by inferred job type. The "default"
# order is used for any job type not listed.
PROJECT_ORDER_BY_JOB_TYPE = {
    "default": [
        "project_truthful_resume_agent",
        "project_emotion_pixel_eval",
        "project_chinese_learning_mvp",
        "project_dl_learning_lab",
    ],
    "Algorithm / multimodal research": [
        "project_emotion_pixel_eval",
        "project_dl_learning_lab",
        "project_chinese_learning_mvp",
    ],
}

# Maximum entries per section.
SECTION_LIMITS = {"实习经历": 3, "项目经历": 3}


def _sort_unknown(fragments: dict[str, ResumeFragment], ids: set[str]) -> list[str]:
    """Sort fragments not in the hardcoded order by display_priority then fact_id."""
    return sorted(
        ids,
        key=lambda fid: (
            fragments[fid].display_priority if fragments[fid].display_priority is not None else 999,
            fid,
        ),
    )


def resolve_internship_order(fragments: dict[str, ResumeFragment]) -> tuple[str, ...]:
    """Return internship display order: hardcoded preferred + unknown fragments appended."""
    known = [fid for fid in INTERNSHIP_ORDER if fid in fragments]
    unknown = set(fragments) - set(INTERNSHIP_ORDER)
    unknown_internships = {fid for fid in unknown if fragments[fid].section == "实习经历"}
    return (*known, *_sort_unknown(fragments, unknown_internships))


def resolve_project_order(
    job_type: str,
    fragments: dict[str, ResumeFragment],
) -> list[str]:
    """Return project display order: hardcoded preferred + unknown fragments appended."""
    hardcoded = PROJECT_ORDER_BY_JOB_TYPE.get(job_type, PROJECT_ORDER_BY_JOB_TYPE["default"])
    known = [fid for fid in hardcoded if fid in fragments]
    unknown = set(fragments) - set(hardcoded)
    unknown_projects = {fid for fid in unknown if fragments[fid].section == "项目经历"}
    return [*known, *_sort_unknown(fragments, unknown_projects)]

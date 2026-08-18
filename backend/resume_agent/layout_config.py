"""Resume layout configuration.

Keeps display ordering, composite-fragment sources, and section limits out of
the generator/selection logic so that adding a new fact or fragment does not
require editing those modules.
"""

from __future__ import annotations

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
SECTION_LIMITS = {"实习经历": 3, "项目经历": 2}

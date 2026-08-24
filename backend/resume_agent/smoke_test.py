from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

from .analyzer import AnalysisResult, FactMatch, build_recommendations, merge_keyword_floor
from .aeo_review import build_aeo_review, build_term_coverage, latex_to_screening_text
from .authorization_store import (
    apply_reusable_authorizations,
    authorization_path,
    record_authorizations_from_review,
)
from .cli import main as cli_main
from .canonical import audit_canonical_resume, register_canonical, write_canonical_audit
from .decision_flow import run_interactive_decision
from .eval_matchers import score_matches, validate_label_coverage
from .fact_store import load_facts
from .fragments import ResumeFragment, load_fragments
from .gap_trends import diff_against_last, load_snapshots, record_gap_snapshot, render_career_trends_html
from .interview_feedback import (
    append_boundary_to_facts,
    load_feedback,
    record_feedback,
)
from .jd_insight import (
    BONUS_HEADING,
    HARD_REQUIREMENT_HEADING,
    _classify_not_writable,
    _lines_for,
    _sections_by_heading,
    build_jd_insight_data,
    llm_interview_followups,
    llm_phrasing_candidates,
    render_html,
)
from .llm_client import LLMNotConfigured, chat_completion, get_api_key
from .mastery_history import (
    load_mastery_history,
    record_mastery_snapshot,
    render_mastery_history,
)
from .outcomes import default_outcome_path, load_outcomes, record_outcome
from .profile import EducationProfile, ResumeProfile, SkillProfile, load_profile
from .quality import check_resume_quality
from .review_parser import count_pending_review_items, parse_review_mastery, write_review_state
from .review import render_review_sheet
from .resume_generator import (
    _escape_unescaped_percent,
    _choose_internship_order,
    _choose_project_order,
    _eligible_fragment_levels,
    _select_profile_skills,
)
from .rules import Fact, find_not_writable
from .semantic.guardrails import find_blocked_terms
from .selection import build_selection_plan
from .status import inspect_application, render_status, status_stage


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_JD = PROJECT_ROOT / "data" / "sample_jds" / "tencent_ai_application.md"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_percent_escaping() -> None:
    _assert(_escape_unescaped_percent("提升 2.5%") == r"提升 2.5\%", "bare percent was not escaped")
    _assert(_escape_unescaped_percent(r"精度 50\%") == r"精度 50\%", "escaped percent was double-escaped")


def _assert_internship_composite_selection() -> None:
    fragments = load_fragments()
    combined = {
        "intern_optimization_ai_coding",
        "intern_solver_integration_clarabel",
        "intern_optimization_combined",
        "intern_scip_heuristic_analysis",
        "intern_data_automation",
    }
    order = _choose_internship_order(combined, fragments)
    _assert(order.count("intern_optimization_combined") == 1, "combined internship was not selected")
    _assert("intern_optimization_ai_coding" not in order, "combined internship duplicated AI Coding source")
    _assert("intern_solver_integration_clarabel" not in order, "combined internship duplicated Clarabel source")
    _assert("intern_scip_heuristic_analysis" not in order, "combined internship duplicated SCIP fact slice")
    _assert("intern_data_automation" in order, "unrelated internship was suppressed")

    standalone = _choose_internship_order({"intern_solver_integration_clarabel"}, fragments)
    _assert(
        standalone == ["intern_solver_integration_clarabel"],
        "standalone Clarabel internship was not selectable",
    )


def _assert_project_selection_does_not_silently_drop_confirmed() -> None:
    fragments = load_fragments()
    confirmed = {
        "project_truthful_resume_agent",
        "project_emotion_pixel_eval",
        "project_chinese_learning_mvp",
    }
    order = _choose_project_order("AI application / Agent engineering", confirmed, fragments)
    _assert(order == [
        "project_truthful_resume_agent",
        "project_emotion_pixel_eval",
        "project_chinese_learning_mvp",
    ], "AI project ordering silently dropped an A/B-confirmed project")

    facts = [
        Fact("fact_a", "Matched project A", ("Agent",), "A", (), "low"),
        Fact("fact_b", "Matched project B", ("RAG",), "B", (), "low"),
    ]
    result = AnalysisResult(
        job_type="AI application / Agent engineering",
        strong_matches=[FactMatch(facts[0], ["Agent"], "strong")],
        weak_matches=[FactMatch(facts[1], ["RAG"], "weak")],
        not_writable={},
        recommendations=[],
        risks=[],
    )
    recommendations = build_recommendations(result)
    rendered = "\n".join(recommendations)
    _assert("Matched project A, Matched project B" in rendered, "recommendations ignored actual matches")
    _assert("Lead with AI product MVP" not in rendered, "stale fixed strategy text remained")

    future_order = _choose_project_order(
        "AI application / Agent engineering",
        {"project_truthful_resume_agent", "project_future"},
        fragments,
    )
    _assert(future_order[-1] == "project_future", "new project IDs were silently dropped")
    _assert(
        _choose_internship_order({"intern_future"}, fragments) == ["intern_future"],
        "new internship IDs were silently dropped",
    )


def _assert_profile_skills_require_confirmed_fact_sources() -> None:
    profile = ResumeProfile(
        name="Candidate",
        birth="2000年1月",
        phone="13800000000",
        email="candidate@example.com",
        photo_source="",
        education=EducationProfile(
            date="2022 - 2026",
            school="Example University",
            major="Computer Science",
            details="GPA redacted",
        ),
        awards=(),
        skills=(
            SkillProfile("Supported skill", ("fact_a",)),
            SkillProfile("Unconfirmed skill", ("fact_b",)),
            SkillProfile("Unlinked skill", ()),
            SkillProfile("Unknown skill", ("missing_fact",)),
        ),
        confirmation="desensitized_sample",
        source_path=PROJECT_ROOT / "data" / "profile" / "profile.example.json",
    )
    included, omitted = _select_profile_skills(
        profile,
        mastery={"fact_a": "A", "fact_b": "C"},
        known_fact_ids={"fact_a", "fact_b"},
    )
    _assert([skill.text for skill in included] == ["Supported skill"], "unsupported skill entered resume")
    omitted_by_text = {skill.text: reason for skill, reason in omitted}
    _assert("facts not A/B-confirmed" in omitted_by_text["Unconfirmed skill"], "unconfirmed skill omission was not explained")
    _assert(omitted_by_text["Unlinked skill"] == "no source_fact_ids", "unlinked skill was not identified")
    _assert("unknown fact IDs" in omitted_by_text["Unknown skill"], "unknown skill source was not rejected")


def _copy_required_data(temp_root: Path) -> None:
    for relative in ("data/facts", "data/profile", "data/resume_fragments", "data/semantic_index"):
        source = PROJECT_ROOT / relative
        target = temp_root / relative
        if source.exists():
            shutil.copytree(source, target)

    # A public clone intentionally contains only desensitized *.example.json
    # files. Materialize the same private-runtime filenames inside the isolated
    # smoke fixture so tests exercise both public-clone and private-workspace
    # layouts instead of passing only when the developer has private data.
    runtime_fallbacks = (
        ("data/facts/facts.json", "data/facts/facts.example.json"),
        ("data/resume_fragments/fragments.json", "data/resume_fragments/fragments.example.json"),
        ("data/profile/profile.private.json", "data/profile/profile.example.json"),
    )
    for runtime_relative, example_relative in runtime_fallbacks:
        runtime_path = temp_root / runtime_relative
        example_path = temp_root / example_relative
        if not runtime_path.exists() and example_path.exists():
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(example_path, runtime_path)

    # Keep the profile-skill omission assertion independent from whichever
    # private or desensitized profile happens to exist in the source tree.
    profile_path = temp_root / "data" / "profile" / "profile.private.json"
    if profile_path.exists():
        profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
        profile_data.setdefault("skills", []).append(
            {"text": "Smoke unlinked profile skill", "source_fact_ids": []}
        )
        profile_path.write_text(
            json.dumps(profile_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _run_cli(argv: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli_main(argv)
    return code, stdout.getvalue() + stderr.getvalue()


def _run_decision_with_input(
    review_path: Path,
    side_effect: list[object],
    project_root: Path | None = None,
    revisit_all: bool = False,
) -> tuple[int, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr), patch("builtins.input", side_effect=side_effect):
        code = run_interactive_decision(
            review_path,
            project_root=project_root,
            revisit_all=revisit_all,
        )
    return code, stdout.getvalue() + stderr.getvalue()


def _assert_confirmed_decision_can_be_revisited(temp_root: Path) -> None:
    review_path = temp_root / "revisit_review.md"
    review_path.write_text(
        """# Review

### Existing project choice

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A 本轮采用核心版`
- allowed_resume_intensity: strong
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`
""",
        encoding="utf-8",
    )
    code, output = _run_decision_with_input(
        review_path,
        ["C"],
        project_root=temp_root,
        revisit_all=True,
    )
    _assert(code == 0, f"revisit decision failed:\n{output}")
    _assert("当前选择" in output, "revisit did not show the existing choice")
    _assert(
        parse_review_mastery(review_path) == {"project_chinese_learning_mvp": "C"},
        "revisit did not replace the existing decision",
    )


def _assert_quality_rejects_overfilled_selection(temp_root: Path) -> None:
    fixture = temp_root / "overfilled"
    fixture.mkdir(parents=True, exist_ok=True)
    review_path = fixture / "review_sheet.md"
    review_path.write_text(
        "\n".join(
            f"""### Fact {index}
- fact_id: `fact_{index}`
- mastery_check: `A 本轮采用核心版`
- confirmed_via: `interactive_cli`
"""
            for index in range(5)
        ),
        encoding="utf-8",
    )
    tex_path = fixture / "resume_draft.tex"
    tex_path.write_text(
        r"""\section{实习经历}
\entry{2026}{A}{A}
\begin{resumeItems}\item one\item two\end{resumeItems}
\entry{2025}{B}{B}
\begin{resumeItems}\item three\item four\end{resumeItems}
\section{项目经历}
\projectEntry{2026}{P1}{}{}
\begin{resumeItems}\item five\item six\end{resumeItems}
\projectEntry{2025}{P2}{}{}
\begin{resumeItems}\item seven\item eight\end{resumeItems}
\projectEntry{2024}{P3}{}{}
\begin{resumeItems}\item nine\item ten\end{resumeItems}
\projectEntry{2023}{P4}{}{}
\begin{resumeItems}\item eleven\item twelve\end{resumeItems}
""",
        encoding="utf-8",
    )
    quality = check_resume_quality(review_path, tex_path)
    _assert(not quality.passed, "one-page policy accepted four project entries")
    _assert(
        any("too many project entries" in reason for reason in quality.reasons),
        "overfilled selection did not explain how to revise it",
    )


def _assert_selection_is_id_restricted_and_capacity_bounded(temp_root: Path) -> None:
    fragments = load_fragments(temp_root / "data" / "resume_fragments" / "fragments.json")
    facts = {fact.id: fact for fact in load_facts(temp_root / "data" / "facts" / "facts.json")}
    ordered_ids = [
        "intern_optimization_combined",
        "intern_data_automation",
        "intern_csharp_ai_mvp",
        "project_truthful_resume_agent",
        "project_emotion_pixel_eval",
        "project_chinese_learning_mvp",
        "project_dl_learning_lab",
    ]
    selected_levels = {fragment_id: "A" for fragment_id in ordered_ids}
    result = AnalysisResult(
        job_type="AI application / Agent engineering",
        strong_matches=[],
        weak_matches=[],
        not_writable={},
        recommendations=[],
        risks=[],
    )
    valid_response = """{
      "selected_fragment_ids": [
        "intern_optimization_combined",
        "intern_data_automation",
        "intern_csharp_ai_mvp",
        "project_truthful_resume_agent",
        "project_emotion_pixel_eval",
        "project_chinese_learning_mvp"
      ]
    }"""
    with patch("backend.resume_agent.selection.chat_completion", return_value=valid_response):
        plan = build_selection_plan(
            "JD",
            ordered_ids,
            selected_levels,
            fragments,
            facts,
            result,
            use_llm=True,
        )
    _assert(len(plan.selected_ids) == 6, "restricted selection did not choose the 3+3 capacity")
    _assert(
        plan.omitted_ids == ("project_dl_learning_lab",),
        "restricted selection did not disclose the omitted eligible project",
    )

    invalid_response = '{"selected_fragment_ids":["fabricated_fragment"]}'
    with patch("backend.resume_agent.selection.chat_completion", return_value=invalid_response):
        try:
            build_selection_plan(
                "JD",
                ordered_ids,
                selected_levels,
                fragments,
                facts,
                result,
                use_llm=True,
            )
        except ValueError as exc:
            _assert("unknown fragment IDs" in str(exc), "unknown selection ID failed for the wrong reason")
        else:
            raise AssertionError("LLM selection accepted a fabricated fragment ID")


def _assert_complete_authorized_inventory_is_matcher_independent(temp_root: Path) -> None:
    fragments = load_fragments(temp_root / "data" / "resume_fragments" / "fragments.json")
    review_mastery = {
        "intern_optimization_ai_coding": "A",
        "intern_solver_integration_clarabel": "A",
    }
    global_authorizations = {
        # This internship is deliberately absent from review_mastery: a new
        # JD matcher miss must not erase a previously authorized experience.
        "intern_data_automation": {"level": "A"},
        "intern_csharp_ai_mvp": {"level": "C"},
    }
    levels, excluded = _eligible_fragment_levels(
        fragments,
        review_mastery,
        global_authorizations,
    )
    _assert("intern_optimization_combined" in levels, "current composite review decision was lost")
    _assert("intern_data_automation" in levels, "unmatched globally authorized internship was erased")
    excluded_by_id = {item_id: reason for item_id, _title, reason in excluded}
    _assert("intern_csharp_ai_mvp" in excluded_by_id, "globally blocked internship was not disclosed")


def _assert_canonical_resume_requires_complete_provenance(temp_root: Path) -> None:
    fixture = temp_root / "canonical_fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    tex_path = fixture / "resume.tex"
    pdf_path = fixture / "resume.pdf"
    tex_path.write_text(
        r"""\section{项目经历}
\begin{resumeItems}
  \item \textbf{手工改写：}使用 Python 构建数据自动化流程。
\end{resumeItems}
\section{实习经历}
\begin{resumeItems}
  \item \textbf{接口处理：}对接 REST API 并处理失败重试。
\end{resumeItems}
\section{专业技能}
\begin{resumeItems}
  \item Python
\end{resumeItems}
""",
        encoding="utf-8",
    )
    pdf_path.write_bytes(b"%PDF-1.4\ncanonical fixture\n")
    blocked = audit_canonical_resume(temp_root, "canonical_smoke", tex_path, pdf_path)
    _assert(not blocked.ready and len(blocked.bullets) == 2, "untraced canonical bullets were accepted")
    output_dir = temp_root / "data" / "outputs" / "canonical_smoke"
    _report, _audit_json, todo_path = write_canonical_audit(blocked, output_dir)
    _assert(todo_path is not None and todo_path.exists(), "canonical provenance todo was not written")

    provenance_path = fixture / "provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "bullets": [
                    {
                        "text_sha256": bullet.text_sha256,
                        "source_fact_ids": ["intern_data_automation"],
                        "candidate_confirmed": True,
                    }
                    for bullet in blocked.bullets
                ]
            }
        ),
        encoding="utf-8",
    )
    ready = audit_canonical_resume(
        temp_root,
        "canonical_smoke",
        tex_path,
        pdf_path,
        provenance_path=provenance_path,
    )
    _assert(ready.ready, f"candidate-confirmed canonical provenance remained blocked: {ready.reasons}")
    manifest_path = register_canonical(ready, output_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert(manifest["pdf_sha256"] == hashlib.sha256(pdf_path.read_bytes()).hexdigest(), "canonical PDF hash mismatch")
    _assert(len(manifest["bullets"]) == 2, "canonical manifest lost professional bullets")
    outcome = record_outcome(
        temp_root,
        "canonical_smoke",
        "applied",
        event_date="2026-08-21",
        path=fixture / "outcomes.json",
        resume_path=pdf_path,
    )
    _assert(outcome.resume_sha256 == manifest["pdf_sha256"], "outcome did not hash the actual canonical PDF")
    _assert(outcome.resume_path == str(pdf_path.resolve()), "outcome did not preserve the canonical PDF path")


def _assert_aeo_review_checks_actual_resume_and_parses_screening_model(temp_root: Path) -> None:
    plain = latex_to_screening_text(r"\section{项目经历}\item 使用 RAG 与 FastAPI\%")
    _assert("RAG" in plain and "FastAPI" in plain, "LaTeX screening text extraction lost visible terms")
    coverage = build_term_coverage("要求 RAG、FastAPI、MCP", plain)
    by_term = {item.term: item.status for item in coverage}
    _assert(by_term["RAG"] == "supported_explicit", "fact-backed explicit RAG was misclassified")
    _assert(by_term["FastAPI"] == "supported_explicit", "fact-backed explicit FastAPI was misclassified")
    _assert(by_term["MCP"] == "unsupported_do_not_add", "unsupported missing MCP was not blocked")

    fixture = temp_root / "aeo_fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    jd_path = fixture / "jd.md"
    resume_path = fixture / "resume.tex"
    jd_path.write_text("# 岗位要求\n- 负责 RAG 与 Agent 应用。", encoding="utf-8")
    resume_path.write_text(r"\section{项目经历}\begin{resumeItems}\item 实现 RAG 与 Agent。\end{resumeItems}", encoding="utf-8")
    llm_response = json.dumps(
        {
            "persona": "事实约束型 AI 应用工程候选人",
            "red_flags": ["规模化证据有限"],
            "business_problem_fit": ["能够构建受控检索工作流"],
            "likely_misreadings": ["可能被误读为生产级平台"],
            "rewrite_priorities": ["明确本地个人事实库规模"],
        },
        ensure_ascii=False,
    )
    with patch("backend.resume_agent.aeo_review.chat_completion", return_value=llm_response):
        review = build_aeo_review(jd_path, resume_path, use_llm=True)
    _assert(review.persona == "事实约束型 AI 应用工程候选人", "AEO persona parsing failed")
    _assert(review.likely_misreadings, "AEO misreading analysis missing")


def _confirm_all_pending_as_b(review_path: Path) -> None:
    text = review_path.read_text(encoding="utf-8")
    text = text.replace("- mastery_check: `待确认`", "- mastery_check: `B smoke confirmed`")
    text = text.replace("- mastery_check: `降权`", "- mastery_check: `C smoke blocked`")
    text = text.replace("- allowed_resume_intensity: ", "- allowed_resume_intensity: conservative")
    text = text.replace("- allowed_resume_intensity:", "- allowed_resume_intensity: conservative")
    text = text.replace(
        "- allowed_resume_intensity: conservative",
        "- allowed_resume_intensity: conservative\n- confirmed_via: `interactive_cli`\n- confirmed_at: `2000-01-01T00:00:00+00:00`",
    )
    review_path.write_text(text, encoding="utf-8")
    # Sync the JSON sidecar so parse_review_decisions reads the updated state.
    state_path = review_path.with_suffix(".state.json")
    if state_path.exists():
        write_review_state(review_path)


def _assert_semantic_candidates_are_not_mastery_items(temp_root: Path) -> None:
    review_path = temp_root / "semantic_candidate_only.md"
    review_path.write_text(
        """# Review

## Strong Matches
### Writable fact

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A smoke confirmed`

## Semantic Candidates
### Candidate only

- candidate_id: `intern_optimization_ai_coding`
- semantic_signal: semantic_score=0.700, matched_lines=2
- triage_note: semantic-only candidate; not used by decide/generate.
""",
        encoding="utf-8",
    )
    mastery = parse_review_mastery(review_path, require_interactive_confirmation=False)
    _assert(mastery == {"project_chinese_learning_mvp": "A"}, "semantic candidates leaked into mastery parsing")
    verified_mastery = parse_review_mastery(review_path)
    _assert(verified_mastery == {}, "unmarked A/B fact counted as interactively confirmed")


def _assert_jd_insight_structural_extraction() -> None:
    jd_text = """# Role

## 职位要求
1. 熟悉 RAG 知识库构建和上下文注入。
2. 基础条件：计算机相关专业优先。

## 加分项
- 有 MCP 或开源项目经验。
"""
    sections = _sections_by_heading(jd_text)
    hard_lines = _lines_for(sections, HARD_REQUIREMENT_HEADING)
    bonus_lines = _lines_for(sections, BONUS_HEADING)
    _assert(hard_lines == ["熟悉 RAG 知识库构建和上下文注入。"], "hard requirement extraction failed")
    _assert(bonus_lines == ["有 MCP 或开源项目经验。"], "bonus requirement extraction failed")

    no_heading_sections = _sections_by_heading("熟悉 RAG，了解 LangChain。")
    _assert(no_heading_sections == {}, "heading parser should not infer sections from raw text")
    _assert(_lines_for(no_heading_sections, HARD_REQUIREMENT_HEADING) == [], "raw text should not yield hard lines")

    result = AnalysisResult(
        job_type="Unknown / needs manual review",
        strong_matches=[],
        weak_matches=[],
        not_writable={},
        recommendations=[],
        risks=[],
    )
    data = build_jd_insight_data(Path("raw_jd.md"), "熟悉 RAG，了解 LangChain。", result, use_llm=False)
    _assert(not data.structural_split_found, "heading-free JD should degrade instead of guessing structure")
    rendered = render_html(data)
    _assert("LLM 生成" not in rendered, "no-LLM HTML rendered empty LLM cards")
    _assert("JD 原文（点击展开）" in rendered, "raw JD was not moved into a collapsible appendix")
    _assert(rendered.index("和事实库的匹配点") < rendered.index("JD 原文（点击展开）"), "raw JD still dominated the first screen")


def _assert_jd_insight_not_writable_classification() -> None:
    tiers = _classify_not_writable(
        {"RAG": "", "MCP": "", "LangChain": "", "vLLM": ""},
        hard_requirements=["熟悉 RAG 和 LangChain。"],
        bonus_points=["有 MCP 和 LangChain 项目更佳。"],
    )
    _assert(tiers["RAG"] == "hard", "hard-only not-writable term misclassified")
    _assert(tiers["MCP"] == "bonus", "bonus-only not-writable term misclassified")
    _assert(tiers["LangChain"] == "both", "hard+bonus not-writable term misclassified")
    _assert(tiers["vLLM"] == "unknown", "unlocated not-writable term misclassified")

    alternative_tiers = _classify_not_writable(
        {"Go": ""},
        hard_requirements=["掌握 Java、C++、Python、Go 中的至少一门语言，Go 背景优先。"],
        bonus_points=[],
    )
    _assert(alternative_tiers["Go"] == "bonus", "alternative/preferred language misclassified as hard")


def _assert_not_writable_terms_are_evidence_checks() -> None:
    jd_text = "要求具备 RAG 和向量数据库 经验，也熟悉 LangChain。"
    no_rag_fact = Fact(
        id="prompt_fact",
        title="Prompt-only fact",
        keywords=("Prompt",),
        summary="Built prompt-only JSON output constraints.",
        boundaries=("No retrieval system.",),
        risk="medium",
    )
    unsupported = find_not_writable(jd_text, [no_rag_fact])
    _assert("RAG" in unsupported, "RAG should be blocked when no fact backs it")
    _assert("向量数据库" in unsupported, "向量数据库 should be blocked when no fact backs it")
    _assert("LangChain" in unsupported, "LangChain should remain blocked without evidence")

    rag_fact = Fact(
        id="rag_fact",
        title="RAG fact",
        keywords=("RAG", "向量数据库", "vector database", "Qdrant"),
        summary="Built a RAG workflow with Qdrant vector database retrieval and fact_id citation checks.",
        boundaries=("No LangChain implementation.",),
        risk="medium",
    )
    supported = find_not_writable(jd_text, [no_rag_fact, rag_fact])
    _assert("RAG" not in supported, "RAG should unblock once a fact backs it")
    _assert("向量数据库" not in supported, "向量数据库 should unblock once a fact backs it")
    _assert("LangChain" in supported, "unrelated unsupported terms should remain blocked")

    blocked_terms = dict(find_blocked_terms(jd_text, facts=[no_rag_fact, rag_fact]))
    _assert("RAG" not in blocked_terms, "semantic guardrail did not share RAG evidence check")
    _assert("向量数据库" not in blocked_terms, "semantic guardrail did not share vector DB evidence check")
    _assert("LangChain" in blocked_terms, "semantic guardrail failed to keep unsupported LangChain blocked")

    product_gaps = find_not_writable(
        "加分项：了解 3D 打印，做过硬件项目，熟悉 MicroPython。",
        [no_rag_fact],
    )
    _assert("3D 打印" in product_gaps, "unsupported 3D-printing gap was not blocked")
    _assert("硬件项目" in product_gaps, "unsupported hardware-project gap was not blocked")
    _assert("MicroPython" in product_gaps, "unsupported MicroPython gap was not blocked")

    engineering_gaps = find_not_writable(
        "要求 MySQL、分布式、高并发、高可用、K8s、消息队列、微调与多智能体经验。",
        [no_rag_fact],
    )
    for term in ("MySQL", "分布式", "高并发", "高可用", "K8s", "消息队列", "微调", "多智能体"):
        _assert(term in engineering_gaps, f"unsupported engineering gap was not blocked: {term}")


def _assert_keyword_floor_merges_with_semantic_matches() -> None:
    semantic_fact = Fact(
        id="semantic_fact",
        title="Semantic fact",
        keywords=("AI Coding",),
        summary="Used AI coding tools for codebase analysis.",
        boundaries=("Manual verification required.",),
        risk="medium",
    )
    rag_fact = Fact(
        id="rag_fact",
        title="RAG fact",
        keywords=("RAG",),
        summary="Built a bounded RAG flow.",
        boundaries=("No autonomous resume writing.",),
        risk="high",
    )
    semantic_match = FactMatch(
        fact=semantic_fact,
        matched_keywords=["semantic_score=0.620", "matched_lines=2"],
        level="strong",
    )
    duplicate_keyword_match = FactMatch(
        fact=semantic_fact,
        matched_keywords=["AI Coding"],
        level="weak",
    )
    keyword_only_match = FactMatch(
        fact=rag_fact,
        matched_keywords=["RAG"],
        level="weak",
    )

    merged = merge_keyword_floor([semantic_match], [duplicate_keyword_match, keyword_only_match])
    by_id = {match.fact.id: match for match in merged}
    _assert(set(by_id) == {"semantic_fact", "rag_fact"}, "keyword floor lost or duplicated facts")
    _assert(by_id["semantic_fact"].level == "strong", "keyword floor downgraded semantic strong match")
    _assert("AI Coding" in by_id["semantic_fact"].matched_keywords, "keyword labels were not merged")
    _assert(by_id["rag_fact"].matched_keywords == ["RAG"], "keyword-only fact was not preserved")


def _assert_review_parser_supports_composite_fact_blocks(temp_root: Path) -> None:
    review_path = temp_root / "composite_review.md"
    review_path.write_text(
        """# Review

### Combined project

- display_fact_id: `combined_project`
- fact_id: `source_a`
- fact_id: `source_b`
- mastery_check: `B smoke confirmed`
- confirmed_via: `interactive_cli`
""",
        encoding="utf-8",
    )
    mastery = parse_review_mastery(review_path)
    _assert(mastery == {"source_a": "B", "source_b": "B"}, "composite review block did not confirm both source facts")


def _assert_resume_authorization_reuse_is_content_bound(temp_root: Path) -> None:
    """授权可跨申请复用；检索元数据变化不重问，事实/文案变化重问。"""
    auth_root = temp_root / "authorization_reuse"
    shutil.copytree(temp_root / "data" / "facts", auth_root / "data" / "facts")
    shutil.copytree(
        temp_root / "data" / "resume_fragments",
        auth_root / "data" / "resume_fragments",
    )

    first_review = auth_root / "data" / "outputs" / "first" / "review_sheet.md"
    first_review.parent.mkdir(parents=True, exist_ok=True)
    first_review.write_text(
        """# Review

### Chinese learning project

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A 事实与核心版文案准确，授权用于简历`
- allowed_options: A/B/C/D
- allowed_resume_intensity: strong
- confirmed_via: `interactive_cli`
- confirmed_at: `2026-08-20T00:00:00+00:00`
""",
        encoding="utf-8",
    )
    recorded = record_authorizations_from_review(auth_root, first_review)
    _assert(recorded == 1, f"expected one reusable authorization, got {recorded}")
    _assert(authorization_path(auth_root).exists(), "authorization store was not created")

    second_review = auth_root / "data" / "outputs" / "second" / "review_sheet.md"
    second_review.parent.mkdir(parents=True, exist_ok=True)
    pending_review = """# Review

### Chinese learning project

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `待确认`
- allowed_options: A/B/C/D
- allowed_resume_intensity:
"""
    second_review.write_text(pending_review, encoding="utf-8")
    reused = apply_reusable_authorizations(auth_root, second_review)
    _assert(reused == 1, f"unchanged authorization was not reused: {reused}")
    _assert(count_pending_review_items(second_review) == 0, "reused authorization remained pending")
    _assert(
        parse_review_mastery(second_review) == {"project_chinese_learning_mvp": "A"},
        "reused authorization did not remain generator-eligible",
    )

    import json as _json

    facts_path = auth_root / "data" / "facts" / "facts.json"
    facts = _json.loads(facts_path.read_text(encoding="utf-8"))
    fact = next(item for item in facts if item["id"] == "project_chinese_learning_mvp")
    fact["keywords"].append("retrieval-only-keyword")
    facts_path.write_text(_json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    keyword_review = auth_root / "data" / "outputs" / "keyword_only" / "review_sheet.md"
    keyword_review.parent.mkdir(parents=True, exist_ok=True)
    keyword_review.write_text(pending_review, encoding="utf-8")
    reused_after_keyword_change = apply_reusable_authorizations(auth_root, keyword_review)
    _assert(
        reused_after_keyword_change == 1,
        "retrieval-only fact keyword change incorrectly invalidated wording authorization",
    )

    fragments_path = auth_root / "data" / "resume_fragments" / "fragments.json"
    fragments = _json.loads(fragments_path.read_text(encoding="utf-8"))
    fragment = next(item for item in fragments if item["fact_id"] == "project_chinese_learning_mvp")
    fragment["bullets"]["A"][0] += " changed"
    fragments_path.write_text(_json.dumps(fragments, ensure_ascii=False, indent=2), encoding="utf-8")

    changed_review = auth_root / "data" / "outputs" / "changed" / "review_sheet.md"
    changed_review.parent.mkdir(parents=True, exist_ok=True)
    changed_review.write_text(pending_review, encoding="utf-8")
    reused_after_change = apply_reusable_authorizations(auth_root, changed_review)
    _assert(reused_after_change == 0, "changed fragment incorrectly reused an old authorization")
    _assert(count_pending_review_items(changed_review) == 1, "changed fragment did not require confirmation")


def _assert_review_renders_composite_across_match_levels() -> None:
    cli_fact = Fact(
        id="project_truthful_resume_agent_cli",
        title="Workflow fact",
        keywords=("Python",),
        summary="Built a local CLI workflow.",
        boundaries=("Not a SaaS product.",),
        risk="medium",
    )
    rag_fact = Fact(
        id="project_truthful_resume_agent_rag_qdrant",
        title="RAG fact",
        keywords=("RAG",),
        summary="Built a bounded RAG workflow.",
        boundaries=("Not a production RAG platform.",),
        risk="high",
    )
    review = render_review_sheet(
        AnalysisResult(
            job_type="AI application / Agent engineering",
            strong_matches=[FactMatch(fact=cli_fact, matched_keywords=["Python"], level="strong")],
            weak_matches=[FactMatch(fact=rag_fact, matched_keywords=["RAG"], level="weak")],
            not_writable={},
            recommendations=[],
            risks=[],
        ),
        jd_path=PROJECT_ROOT / "data" / "sample_jds" / "alibaba_ai_agent_engineer.md",
        project_root=PROJECT_ROOT,
    )
    _assert(review.count("display_fact_id: `project_truthful_resume_agent`") == 1, "composite display block missing")
    _assert(
        review.count("Truthful Resume Agent：面向 JD 的真实经历匹配与 RAG 辅助工具") == 1,
        "composite project rendered more than once",
    )
    _assert(review.count("- fact_id: `project_truthful_resume_agent_cli`") == 1, "CLI source fact missing")
    _assert(review.count("- fact_id: `project_truthful_resume_agent_rag_qdrant`") == 1, "RAG source fact missing")
    _assert(review.count("- fact_id: `project_truthful_resume_agent_agent`") == 1, "Agent source fact missing")
    _assert(review.count("- mastery_check: `待确认`") == 1, "composite strong/weak split should ask once")


def _assert_review_expands_partially_matched_composite() -> None:
    facts = {
        fact.id: fact
        for fact in load_facts(PROJECT_ROOT / "data" / "facts" / "facts.json")
    }
    review = render_review_sheet(
        AnalysisResult(
            job_type="AI application / Agent engineering",
            strong_matches=[],
            weak_matches=[
                FactMatch(
                    fact=facts["intern_optimization_ai_coding"],
                    matched_keywords=["Claude Code"],
                    level="weak",
                )
            ],
            not_writable={},
            recommendations=[],
            risks=[],
        ),
        jd_path=PROJECT_ROOT / "data" / "sample_jds" / "ai_agent_engineer.md",
        project_root=PROJECT_ROOT,
    )
    _assert(review.count("display_fact_id: `intern_optimization_combined`") == 1, "partial composite was not coalesced")
    _assert(review.count("- fact_id: `intern_optimization_ai_coding`") == 1, "matched composite source missing")
    _assert(review.count("- fact_id: `intern_solver_integration_clarabel`") == 1, "unmatched composite source was not included for review")
    _assert(review.count("- mastery_check: `降权`") == 1, "partial composite should still ask once")


def _sample_fact_match() -> FactMatch:
    fact = Fact(
        id="real_fact",
        title="Real fact",
        keywords=("Prompt",),
        summary="Built a prompt-constrained JSON workflow.",
        boundaries=("Do not claim RAG.",),
        risk="medium",
    )
    return FactMatch(fact=fact, matched_keywords=["Prompt"], level="weak")


def _assert_matcher_metrics_count_false_positives() -> None:
    useful = _sample_fact_match()
    irrelevant_fact = Fact(
        id="irrelevant_fact",
        title="Irrelevant fact",
        keywords=("Python",),
        summary="A topically similar fact that does not support this role.",
        boundaries=("Do not select for this role.",),
        risk="medium",
    )
    irrelevant = FactMatch(fact=irrelevant_fact, matched_keywords=["semantic_score=0.6"], level="strong")
    metrics = score_matches(
        [useful, irrelevant],
        {
            "real_fact": {"label": "useful"},
            "irrelevant_fact": {"label": "irrelevant"},
            "missed_fact": {"label": "useful"},
        },
    )
    _assert(metrics.useful_precision == 0.5, "matcher metrics did not count the irrelevant selection")
    _assert(metrics.useful_recall == 0.5, "matcher metrics did not count the missed useful fact")
    _assert(metrics.irrelevant_ids == ("irrelevant_fact",), "matcher metrics lost irrelevant fact ids")
    _assert(metrics.missed_useful_ids == ("missed_fact",), "matcher metrics lost missed useful fact ids")


def _assert_matcher_labels_must_cover_current_corpus() -> None:
    jd_paths = [Path("a.md")]
    complete = {
        "a.md": {
            "fact_a": {"label": "useful", "rationale": "direct evidence"},
            "fact_b": {"label": "irrelevant", "rationale": "no role support"},
        }
    }
    _assert(
        validate_label_coverage(jd_paths, {"fact_a", "fact_b"}, complete) == [],
        "complete matcher labels were rejected",
    )
    errors = validate_label_coverage(jd_paths, {"fact_a", "fact_b"}, {"a.md": {"fact_a": complete["a.md"]["fact_a"]}})
    _assert(any("missing fact labels: fact_b" in error for error in errors), "stale matcher labels were not blocked")


def _assert_llm_phrasing_candidates_are_advisory_and_screened() -> None:
    fake_response = """[
  {"fact_id": "real_fact", "phrasing": "Built a prompt-constrained JSON workflow."},
  {"fact_id": "fabricated_fact", "phrasing": "Built a production RAG platform."}
]"""
    with patch("backend.resume_agent.jd_insight.chat_completion", side_effect=[fake_response, "OK"]):
        accepted, rejected = llm_phrasing_candidates([_sample_fact_match()])
    _assert(
        accepted == [{"fact_id": "real_fact", "phrasing": "Built a prompt-constrained JSON workflow."}],
        "screened advisory phrasing was lost",
    )
    _assert(len(rejected) == 1 and "fabricated_fact" in rejected[0], "unknown fact_id was not dropped")


def _assert_llm_interview_followups_only_render_questions() -> None:
    fake_response = """[
      {
        "fact_id": "real_fact",
        "questions": ["你如何核验 JSON 输出是否符合约束？"],
        "ideal_answer": "使用并不存在的 1000 个样本和生产级监控。"
      },
      {
        "fact_id": "fabricated_fact",
        "questions": ["请介绍虚构的 RAG 平台。"]
      }
    ]"""
    with patch("backend.resume_agent.jd_insight.chat_completion", return_value=fake_response):
        rendered, error = llm_interview_followups("需要 Prompt 工程能力。", [_sample_fact_match()])
    _assert(error is None and rendered is not None, "valid interview question was not rendered")
    _assert("如何核验 JSON 输出" in rendered, "accepted interview question was lost")
    _assert("1000 个样本" not in rendered, "LLM answer/example field leaked into interview questions")
    _assert("fabricated_fact" not in rendered and "虚构的 RAG" not in rendered, "unknown fact_id question was not dropped")


def _assert_llm_phrasing_boundary_screen_fails_closed() -> None:
    fake_response = """[
  {"fact_id": "real_fact", "phrasing": "Built a production-grade RAG platform."}
]"""
    with patch(
        "backend.resume_agent.jd_insight.chat_completion",
        side_effect=[fake_response, "VIOLATION: claims RAG, boundary forbids it"],
    ):
        accepted, rejected = llm_phrasing_candidates([_sample_fact_match()])
    _assert(accepted == [], "boundary-screened phrasing was incorrectly retained")
    _assert(len(rejected) == 1 and "边界风险筛查" in rejected[0], "boundary rejection reason was lost")


def _assert_llm_not_configured_degrades(temp_root: Path) -> None:
    missing_env = temp_root / "missing.env"
    with patch.dict(os.environ, {}, clear=True), patch("backend.resume_agent.llm_client.ENV_PATH", missing_env):
        try:
            get_api_key()
        except LLMNotConfigured as exc:
            _assert("DEEPSEEK_API_KEY not set" in str(exc), "LLMNotConfigured message was not explicit")
        else:
            raise AssertionError("get_api_key should fail without env var or .env")

        result = AnalysisResult(
            job_type="AI application / Agent engineering",
            strong_matches=[],
            weak_matches=[],
            not_writable={},
            recommendations=[],
            risks=[],
        )
        data = build_jd_insight_data(Path("jd.md"), "# 职位要求\n1. 熟悉 Prompt。", result, use_llm=True)
    _assert(data.role_summary is None, "missing LLM config should not produce a role summary")
    _assert(
        data.role_summary_error and "LLM_API_KEY" in data.role_summary_error,
        "missing LLM config did not degrade to an explicit role-summary error",
    )


def _assert_llm_provider_is_configurable() -> None:
    fake_response = type(
        "FakeResponse",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"choices": [{"message": {"content": "ok"}}]},
        },
    )()
    env = {
        "RESUME_AGENT_LLM_API_KEY": "test-key",
        "RESUME_AGENT_LLM_API_URL": "https://example.invalid/chat",
        "RESUME_AGENT_LLM_MODEL": "provider-model-id",
    }
    with patch.dict(os.environ, env, clear=True), patch(
        "backend.resume_agent.llm_client.requests.post", return_value=fake_response
    ) as post:
        _assert(chat_completion([{"role": "user", "content": "test"}]) == "ok", "configured LLM response was lost")
    _assert(post.call_args.args[0] == env["RESUME_AGENT_LLM_API_URL"], "configured LLM URL was ignored")
    _assert(post.call_args.kwargs["json"]["model"] == env["RESUME_AGENT_LLM_MODEL"], "configured LLM model was ignored")


def _write_dense_resume_artifacts(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "resume_draft.tex").write_text(
        r"""\section{实习经历}
\entry{2026.02 - 2026.05}{Role A}{Org A}
\begin{resumeItems}
  \item one
  \item two
\end{resumeItems}
\entry{2025.11 - 2026.01}{Role B}{Org B}
\begin{resumeItems}
  \item three
  \item four
\end{resumeItems}
\section{项目经历}
\projectEntry{2025.02 - 2026.05}{Project A}{}{}
\begin{resumeItems}
  \item five
  \item six
\end{resumeItems}
\projectEntry{2024.10 - 2025.06}{Project B}{}{}
\begin{resumeItems}
  \item seven
  \item eight
\end{resumeItems}
""",
        encoding="utf-8",
    )
    (output_dir / "resume_draft.pdf").write_bytes(b"%PDF-1.4\n% smoke test placeholder\n")


def _assert_status_rejects_stale_artifacts(temp_root: Path) -> None:
    name = "stale_artifact_smoke"
    jd_path = temp_root / "data" / "jd_library" / f"{name}.md"
    output_dir = temp_root / "data" / "outputs" / name
    jd_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    jd_path.write_text("# JD\nPython", encoding="utf-8")
    (output_dir / "match_report.md").write_text("# Match", encoding="utf-8")
    review_path = output_dir / "review_sheet.md"
    review_path.write_text(
        """# Review
### Fact
- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A 本轮采用核心版`
- confirmed_via: `interactive_cli`
""",
        encoding="utf-8",
    )
    tex_path = output_dir / "resume_draft.tex"
    pdf_path = output_dir / "resume_draft.pdf"
    tex_path.write_text("stale tex", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\nstale pdf\n")

    dependency_paths = [
        jd_path,
        temp_root / "data" / "resume_fragments" / "fragments.json",
        temp_root / "data" / "profile" / "profile.private.json",
        Path(__file__).with_name("resume_generator.py"),
    ]
    baseline = max(path.stat().st_mtime_ns for path in dependency_paths if path.exists()) + 10_000_000
    os.utime(tex_path, ns=(baseline, baseline))
    os.utime(pdf_path, ns=(baseline + 1, baseline + 1))
    os.utime(review_path, ns=(baseline + 2, baseline + 2))

    status = inspect_application(temp_root, name)
    _assert(not status.tex_fresh and not status.pdf_fresh, "review changes did not stale generated artifacts")
    _assert(status_stage(status) == "finalize", "stale artifacts were reported as export-ready")
    _assert("Resume draft tex: stale" in render_status(status), "stale TeX label was not visible")


def _assert_fact_changes_stale_review(temp_root: Path) -> None:
    name = "stale_review_smoke"
    jd_path = temp_root / "data" / "jd_library" / f"{name}.md"
    output_dir = temp_root / "data" / "outputs" / name
    jd_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    jd_path.write_text("# JD\nPython", encoding="utf-8")
    (output_dir / "match_report.md").write_text("# Match", encoding="utf-8")
    review_path = output_dir / "review_sheet.md"
    review_path.write_text(
        """# Review
- fact_id: `intern_data_automation`
- mastery_check: `A smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`
""",
        encoding="utf-8",
    )
    review_time = max(
        path.stat().st_mtime_ns
        for path in (
            jd_path,
            temp_root / "data" / "facts" / "facts.json",
            temp_root / "data" / "resume_fragments" / "fragments.json",
        )
    ) + 10_000_000
    os.utime(review_path, ns=(review_time, review_time))
    status = inspect_application(temp_root, name)
    _assert(status.review_fresh, "new review was unexpectedly stale")

    facts_path = temp_root / "data" / "facts" / "facts.json"
    original_facts_mtime = facts_path.stat().st_mtime_ns
    os.utime(facts_path, ns=(review_time + 10_000_000, review_time + 10_000_000))
    stale = inspect_application(temp_root, name)
    _assert(not stale.review_fresh, "fact change did not stale the review")
    _assert(status_stage(stale) == "prepare", "stale review did not return workflow to prepare")
    _assert("Review sheet: stale" in render_status(stale), "stale review label was not visible")
    finalize_code, finalize_output = _run_cli(
        ["finalize", "--name", name, "--project-root", str(temp_root)]
    )
    _assert(finalize_code == 2, "finalize accepted a stale review")
    _assert("review sheet is stale" in finalize_output, "finalize did not explain stale-review rejection")
    os.utime(facts_path, ns=(original_facts_mtime, original_facts_mtime))


def _write_deliverable_fixture(temp_root: Path) -> None:
    output_dir = temp_root / "data" / "outputs" / "deliverable_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_sheet.md").write_text(
        """# Review

### A fact

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`

### B fact

- fact_id: `intern_optimization_ai_coding`
- mastery_check: `B smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`
""",
        encoding="utf-8",
    )
    _write_dense_resume_artifacts(output_dir)


def _write_unverified_fixture(temp_root: Path) -> None:
    output_dir = temp_root / "data" / "outputs" / "unverified_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_sheet.md").write_text(
        """# Review

### Hand edited A fact

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A smoke confirmed`

### Hand edited B fact

- fact_id: `intern_optimization_ai_coding`
- mastery_check: `B smoke confirmed`
""",
        encoding="utf-8",
    )
    _write_dense_resume_artifacts(output_dir)


def _write_semantic_origin_fixture(temp_root: Path) -> None:
    jd_dir = temp_root / "data" / "jd_library"
    jd_dir.mkdir(parents=True, exist_ok=True)
    (jd_dir / "semantic_origin_smoke.md").write_text(
        """# Role

- 能把业务需求拆成可演示的学习产品原型。
- 能和团队沟通页面流程、对话体验、演示边界。
""",
        encoding="utf-8",
    )
    output_dir = temp_root / "data" / "outputs" / "semantic_origin_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_sheet.md").write_text(
        """# Review

### Semantic-origin confirmed fact

- fact_id: `project_chinese_learning_mvp`
- mastery_check: `A smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`
""",
        encoding="utf-8",
    )


def _write_composite_fragment_fixture(temp_root: Path) -> None:
    jd_dir = temp_root / "data" / "jd_library"
    jd_dir.mkdir(parents=True, exist_ok=True)
    (jd_dir / "composite_fragment_smoke.md").write_text(
        """# Role

- 需要 RAG 知识库构建和 JD 理解能力。
- 需要能解释大模型输出边界和人工确认流程。
""",
        encoding="utf-8",
    )
    output_dir = temp_root / "data" / "outputs" / "composite_fragment_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_sheet.md").write_text(
        """# Review

### Truthful Resume Agent workflow

- fact_id: `project_truthful_resume_agent_cli`
- mastery_check: `B smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`

### Truthful Resume Agent RAG layer

- fact_id: `project_truthful_resume_agent_rag_qdrant`
- mastery_check: `B smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`

### Truthful Resume Agent conversational agent

- fact_id: `project_truthful_resume_agent_agent`
- mastery_check: `B smoke confirmed`
- confirmed_via: `interactive_cli`
- confirmed_at: `2000-01-01T00:00:00+00:00`
""",
        encoding="utf-8",
    )


def run_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="truthful_resume_smoke_") as raw_temp:
        temp_root = Path(raw_temp)
        _copy_required_data(temp_root)

        _assert_internship_composite_selection()
        _assert_percent_escaping()
        _assert_project_selection_does_not_silently_drop_confirmed()
        _assert_profile_skills_require_confirmed_fact_sources()
        _assert_confirmed_decision_can_be_revisited(temp_root)
        _assert_quality_rejects_overfilled_selection(temp_root)
        _assert_selection_is_id_restricted_and_capacity_bounded(temp_root)
        _assert_complete_authorized_inventory_is_matcher_independent(temp_root)
        _assert_canonical_resume_requires_complete_provenance(temp_root)
        _assert_aeo_review_checks_actual_resume_and_parses_screening_model(temp_root)
        _assert_jd_insight_structural_extraction()
        _assert_jd_insight_not_writable_classification()
        _assert_not_writable_terms_are_evidence_checks()
        _assert_keyword_floor_merges_with_semantic_matches()
        _assert_matcher_metrics_count_false_positives()
        _assert_matcher_labels_must_cover_current_corpus()
        _assert_review_parser_supports_composite_fact_blocks(temp_root)
        _assert_resume_authorization_reuse_is_content_bound(temp_root)
        _assert_review_renders_composite_across_match_levels()
        _assert_review_expands_partially_matched_composite()
        _assert_llm_phrasing_candidates_are_advisory_and_screened()
        _assert_llm_interview_followups_only_render_questions()
        _assert_llm_phrasing_boundary_screen_fails_closed()
        _assert_llm_not_configured_degrades(temp_root)
        _assert_llm_provider_is_configurable()
        _assert_status_rejects_stale_artifacts(temp_root)
        _assert_fact_changes_stale_review(temp_root)
        _assert_interview_feedback_roundtrip(temp_root)
        _assert_gap_trends_snapshot_diff(temp_root)
        _assert_mastery_history_progression(temp_root)

        prepare_code, prepare_output = _run_cli(
            [
                "prepare",
                "--file",
                str(SAMPLE_JD),
                "--name",
                "smoke_tencent",
                "--no-semantic-candidates",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(prepare_code == 0, f"prepare command failed:\n{prepare_output}")

        output_dir = temp_root / "data" / "outputs" / "smoke_tencent"
        review_path = output_dir / "review_sheet.md"
        report_path = output_dir / "match_report.md"
        _assert(report_path.exists(), "prepare did not write match_report.md")
        _assert(review_path.exists(), "prepare did not write review_sheet.md")

        status_code, status_output = _run_cli(
            [
                "status",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(status_code == 0, f"status command failed after prepare:\n{status_output}")
        _assert("Next step: decide:" in status_output, "status did not point to decide after prepare")

        list_code, list_output = _run_cli(
            [
                "list",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(list_code == 0, f"list command failed after prepare:\n{list_output}")
        _assert("smoke_tencent" in list_output, "list did not include prepared application")
        _assert("decide" in list_output, "list did not show decide stage after prepare")

        outcome_code, outcome_output = _run_cli(
            [
                "record-outcome",
                "--name",
                "smoke_tencent",
                "--status",
                "applied",
                "--date",
                "2000-01-01",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(outcome_code == 0, f"record-outcome failed:\n{outcome_output}")
        outcome_events = load_outcomes(default_outcome_path(temp_root))
        _assert(len(outcome_events) == 1, "record-outcome did not persist exactly one event")
        _assert(outcome_events[0].resume_sha256 is None, "outcome invented a hash before a PDF existed")
        invalid_outcome_code, invalid_outcome_output = _run_cli(
            [
                "record-outcome",
                "--name",
                "smoke_tencent",
                "--status",
                "applied",
                "--date",
                "not-a-date",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(invalid_outcome_code == 2, "record-outcome accepted an invalid date")
        _assert("YYYY-MM-DD" in invalid_outcome_output, "invalid outcome date error was unclear")

        pending_finalize_code, _ = _run_cli(
            [
                "finalize",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(pending_finalize_code == 2, "finalize should reject an unconfirmed review sheet")

        with patch("sys.stdin.isatty", return_value=False):
            non_tty_decide_code, non_tty_decide_output = _run_cli(
                [
                    "decide",
                    "--name",
                    "smoke_tencent",
                    "--project-root",
                    str(temp_root),
                ]
            )
        _assert(non_tty_decide_code == 2, "CLI decide should reject non-TTY stdin")
        _assert("requires a real terminal" in non_tty_decide_output, "CLI decide did not explain the TTY requirement")

        original_review = review_path.read_text(encoding="utf-8")
        eof_decide_code, eof_decide_output = _run_decision_with_input(review_path, [EOFError()], temp_root)
        _assert(eof_decide_code == 0, f"interactive decision should handle EOF without crashing:\n{eof_decide_output}")
        _assert("输入已结束" in eof_decide_output, "decide did not explain EOF exit")
        _assert(review_path.read_text(encoding="utf-8") == original_review, "EOF decide changed the review sheet")

        choice_only_code, choice_only_output = _run_decision_with_input(review_path, ["B", EOFError()], temp_root)
        _assert(choice_only_code == 0, f"choice-only decide should stop cleanly on EOF:\n{choice_only_output}")
        _assert("已更新 1 条确认结果" in choice_only_output, "choice-only decide did not record one answer")
        _assert("A 核心版将写成" in choice_only_output, "decide did not show the concrete A wording")
        _assert("B 保守版将写成" in choice_only_output, "decide did not show the concrete B wording")
        choice_only_review = review_path.read_text(encoding="utf-8")
        _assert("confirmed_via: `interactive_cli`" in choice_only_review, "choice-only decide missed confirmation marker")
        _assert(
            "what_i_can_explain: 能讲" not in choice_only_review,
            "choice-only decide unexpectedly collected free-text notes",
        )
        resumed_code, resumed_output = _run_decision_with_input(review_path, [EOFError()], temp_root)
        _assert(resumed_code == 0, f"resumed decide should stop cleanly on EOF:\n{resumed_output}")
        _assert(
            resumed_output.count("AI Chinese learning mini program MVP") == 0,
            "resumed decide repeated an already confirmed fact",
        )
        review_path.write_text(original_review, encoding="utf-8")
        # Remove stale sidecar so parse_review_decisions reads the restored markdown.
        state_path = review_path.with_suffix(".state.json")
        if state_path.exists():
            state_path.unlink()

        _confirm_all_pending_as_b(review_path)
        mastery = parse_review_mastery(review_path)
        _assert(
            mastery.get("project_chinese_learning_mvp") == "B",
            "marked review did not parse project_chinese_learning_mvp as B",
        )

        finalize_code, finalize_output = _run_cli(
            [
                "finalize",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(finalize_code == 0, f"finalize command failed after confirmation:\n{finalize_output}")

        tex_path = output_dir / "resume_draft.tex"
        _assert(tex_path.exists(), "finalize did not write resume_draft.tex")
        tex_text = tex_path.read_text(encoding="utf-8")
        _assert("LangChain" in tex_text, "fact-backed LangChain Agent evidence was omitted from generated resume")
        _assert("MCP" not in tex_text, "not-writable MCP leaked into generated resume")
        _assert("Smoke unlinked profile skill" not in tex_text, "unlinked profile skills leaked into generated resume")
        selection_text = (output_dir / "selection_plan.md").read_text(encoding="utf-8")
        _assert("## Profile Skills" in selection_text, "selection plan omitted the profile-skill audit")
        _assert(
            "Smoke unlinked profile skill: no source_fact_ids" in selection_text,
            "selection plan did not explain the unlinked skill omission",
        )

        final_status_code, final_status_output = _run_cli(
            [
                "status",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(final_status_code == 0, f"status command failed after finalize:\n{final_status_output}")
        _assert("Resume draft tex: yes" in final_status_output, "status did not detect generated tex")
        _assert("Next step: optional pdf:" in final_status_output, "status did not point to optional PDF after tex generation")

        final_list_code, final_list_output = _run_cli(
            [
                "list",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(final_list_code == 0, f"list command failed after finalize:\n{final_list_output}")
        _assert("tex_ready" in final_list_output, "list did not show tex_ready after finalize")

        (output_dir / "resume_draft.pdf").write_bytes(b"%PDF-1.4\n% smoke test placeholder\n")
        deliver_root = temp_root / "delivery"
        thin_deliver_code, thin_deliver_output = _run_cli(
            [
                "deliver",
                "--name",
                "smoke_tencent",
                "--company",
                "腾讯",
                "--role",
                "AI应用开发工程师",
                "--delivery-root",
                str(deliver_root),
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(thin_deliver_code == 2, "deliver should reject a thin B-only draft")
        _assert("Structure check: needs_review" in thin_deliver_output, "deliver did not print structure failure details")

        gaps_code, gaps_output = _run_cli(
            [
                "gaps",
                "--name",
                "smoke_tencent",
                "--write",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(gaps_code == 0, f"gaps command failed:\n{gaps_output}")
        _assert("Structure check: needs_review" in gaps_output, "gaps did not include structure state")
        _assert("Candidate Facts To Review" in gaps_output, "gaps did not include candidate section")
        _assert("intern_data_automation" in gaps_output, "gaps did not include an unselected internship candidate")
        _assert((output_dir / "gap_report.md").exists(), "gaps --write did not create gap_report.md")

        expand_code, expand_output = _run_cli(
            [
                "expand-review",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(expand_code == 0, f"expand-review command failed:\n{expand_output}")
        added_line = next(
            (line for line in expand_output.splitlines() if line.startswith("Added fact_ids:")),
            "",
        )
        added_ids = {
            fact_id.strip()
            for fact_id in added_line.removeprefix("Added fact_ids:").split(",")
            if fact_id.strip()
        }
        expected_gap_ids = {
            "intern_data_automation",
            "intern_csharp_ai_mvp",
            "project_dl_learning_lab",
        }
        _assert(
            added_ids == expected_gap_ids,
            f"expand-review added unexpected gap candidates: {sorted(added_ids)}",
        )
        expanded_review = review_path.read_text(encoding="utf-8")
        _assert(
            "display_fact_id: `intern_optimization_combined`" in expanded_review,
            "partially matched South Grid composite was not already present before gap expansion",
        )
        _assert(
            expanded_review.count("fact_id: `intern_solver_integration_clarabel`") == 1,
            "Clarabel source should appear once inside the combined South Grid review block",
        )
        _assert("## Gap Review Candidates" in expanded_review, "expand-review did not add review section")
        _assert("fact_id: `intern_data_automation`" in expanded_review, "expand-review missed data automation candidate")
        _assert(
            count_pending_review_items(review_path) == len(expected_gap_ids),
            "expand-review did not create all pending review items",
        )

        pending_gap_finalize_code, pending_gap_finalize_output = _run_cli(
            [
                "finalize",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(pending_gap_finalize_code == 2, "finalize should reject pending gap review candidates")
        _assert("pending item" in pending_gap_finalize_output, "finalize did not explain pending gap rejection")

        _confirm_all_pending_as_b(review_path)
        expanded_finalize_code, expanded_finalize_output = _run_cli(
            [
                "finalize",
                "--name",
                "smoke_tencent",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(expanded_finalize_code == 0, f"finalize failed after gap candidate confirmation:\n{expanded_finalize_output}")
        expanded_tex = tex_path.read_text(encoding="utf-8")
        _assert("数据自动化开发实习生" in expanded_tex, "confirmed gap internship did not enter resume draft")

        _write_unverified_fixture(temp_root)
        unverified_deliver_code, unverified_deliver_output = _run_cli(
            [
                "deliver",
                "--name",
                "unverified_smoke",
                "--company",
                "腾讯",
                "--role",
                "AI应用开发工程师",
                "--delivery-root",
                str(deliver_root),
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(unverified_deliver_code == 2, "deliver should reject hand-edited A/B without confirmation marker")
        _assert(
            "missing interactive confirmation marker" in unverified_deliver_output,
            "deliver did not explain missing confirmation markers",
        )

        _write_deliverable_fixture(temp_root)
        deliver_code, deliver_output = _run_cli(
            [
                "deliver",
                "--name",
                "deliverable_smoke",
                "--company",
                "腾讯",
                "--role",
                "AI应用开发工程师",
                "--delivery-root",
                str(deliver_root),
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(deliver_code == 0, f"deliver command failed:\n{deliver_output}")
        smoke_profile = load_profile(temp_root)
        expected_pdf = (
            deliver_root
            / "腾讯"
            / (
                "腾讯_AI应用开发工程师-"
                f"{smoke_profile.education.school}-"
                f"{smoke_profile.education.major.removesuffix('专业')}-"
                f"{smoke_profile.name}.pdf"
            )
        )
        _assert(expected_pdf.exists(), "deliver did not copy PDF to company folder")
        _assert(
            (deliver_root / "腾讯" / "腾讯_AI应用开发工程师.tex").exists(),
            "deliver did not copy TeX to company folder",
        )

        _assert_semantic_candidates_are_not_mastery_items(temp_root)

        _write_semantic_origin_fixture(temp_root)
        semantic_origin_code, semantic_origin_output = _run_cli(
            [
                "finalize",
                "--name",
                "semantic_origin_smoke",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(
            semantic_origin_code == 0,
            f"semantic-origin review fact vanished during keyword finalize:\n{semantic_origin_output}",
        )
        semantic_origin_tex = (
            temp_root / "data" / "outputs" / "semantic_origin_smoke" / "resume_draft.tex"
        ).read_text(encoding="utf-8")
        _assert(
            "AI 中文学习小程序" in semantic_origin_tex,
            "semantic-origin confirmed fact fragment did not enter generated resume",
        )

        _write_composite_fragment_fixture(temp_root)
        composite_code, composite_output = _run_cli(
            [
                "finalize",
                "--name",
                "composite_fragment_smoke",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(composite_code == 0, f"composite fragment finalize failed:\n{composite_output}")
        composite_tex = (
            temp_root / "data" / "outputs" / "composite_fragment_smoke" / "resume_draft.tex"
        ).read_text(encoding="utf-8")
        _assert(
            composite_tex.count("Truthful Resume Agent") == 1,
            "composite source facts should render as one project entry",
        )
        _assert(
            "Truthful Resume Agent：面向 JD 的真实经历匹配与 RAG 辅助工具" in composite_tex,
            "composite project title missing from generated resume",
        )

        composite_pending_output = temp_root / "data" / "outputs" / "composite_pending_smoke"
        composite_pending_output.mkdir(parents=True, exist_ok=True)
        (temp_root / "data" / "jd_library" / "composite_pending_smoke.md").write_text(
            """# Role

- 需要 Python 和 RAG 项目经验。
""",
            encoding="utf-8",
        )
        (composite_pending_output / "review_sheet.md").write_text(
            """# Review

### Truthful Resume Agent

- fact_id: `project_truthful_resume_agent_cli`
- fact_id: `project_truthful_resume_agent_rag_qdrant`
- fact_id: `project_truthful_resume_agent_agent`
- mastery_check: `待确认`
""",
            encoding="utf-8",
        )
        composite_pending_code, composite_pending_result = _run_cli(
            [
                "generate",
                "--name",
                "composite_pending_smoke",
                "--project-root",
                str(temp_root),
            ]
        )
        _assert(
            composite_pending_code == 2,
            f"pending composite review unexpectedly generated a resume:\n{composite_pending_result}",
        )
        _assert(
            "pending item" in composite_pending_result,
            "pending composite rejection did not explain the review requirement",
        )
        _assert(
            not (composite_pending_output / "resume_draft.tex").exists(),
            "pending composite review wrote an unconfirmed resume draft",
        )


def _run_agent_tests() -> None:
    try:
        from .agent.test_agent import run_all as run_agent_tests

        run_agent_tests()
    except ImportError as exc:
        raise RuntimeError(
            "Agent smoke tests incomplete: install requirements and run with .venv/bin/python"
        ) from exc


def _assert_web_app_available() -> None:
    """FastAPI 层至少必须可导入，避免 Web UI 改坏后 smoke 仍通过。"""
    from .web.app import app as web_app

    _assert(web_app.title == "Truthful Resume Agent", "FastAPI app title mismatch")


def _assert_interview_feedback_roundtrip(temp_root: Path) -> None:
    """面试反馈记录 + 读取 + 损坏 JSON 容错。"""
    feedback = record_feedback(
        project_root=temp_root,
        application="smoke_test_app",
        fact_id="intern_data_automation",
        question="分页机制怎么实现的？",
        note="No pagination in current implementation",
    )
    _assert(feedback.fact_id == "intern_data_automation", "record_feedback did not store fact_id")

    items = load_feedback(temp_root, "smoke_test_app")
    _assert(len(items) == 1, f"load_feedback returned {len(items)} items, expected 1")
    _assert(items[0].question == "分页机制怎么实现的？", "feedback question mismatch")
    try:
        record_feedback(
            project_root=temp_root,
            application="smoke_test_app",
            fact_id="fabricated_fact",
            question="不存在的事实？",
        )
    except ValueError as exc:
        _assert("unknown fact_id" in str(exc), "invalid interview fact failed unclearly")
    else:
        raise AssertionError("interview feedback accepted an unknown fact_id")

    # 损坏 JSON 容错
    feedback_path = temp_root / "data" / "outputs" / "smoke_test_app" / "interview_feedback.json"
    feedback_path.write_text("{invalid json", encoding="utf-8")
    items = load_feedback(temp_root, "smoke_test_app")
    _assert(items == [], "load_feedback did not handle corrupt JSON gracefully")

    facts_path = temp_root / "data" / "facts" / "facts.json"
    result = append_boundary_to_facts(
        facts_path,
        "intern_data_automation",
        "Smoke-test boundary written atomically.",
    )
    _assert(result == "written", f"append_boundary_to_facts returned {result}")
    reloaded_facts = load_facts(facts_path)
    updated = next(fact for fact in reloaded_facts if fact.id == "intern_data_automation")
    _assert(
        "Smoke-test boundary written atomically." in updated.boundaries,
        "atomic boundary update was not persisted",
    )
    _assert(not facts_path.with_name("facts.json.tmp").exists(), "atomic-write temp file leaked")


def _assert_gap_trends_snapshot_diff(temp_root: Path) -> None:
    """缺口快照记录 + diff 对比。"""
    # 第一次快照
    record_gap_snapshot(temp_root, {"RAG": {"jd_a"}})
    history = load_snapshots(temp_root)
    _assert(len(history) == 1, f"expected 1 snapshot, got {len(history)}")

    # 第二次快照：RAG 已补齐，新增 vLLM
    record_gap_snapshot(temp_root, {"vLLM": {"jd_b"}})
    history = load_snapshots(temp_root)
    _assert(len(history) == 2, f"expected 2 snapshots, got {len(history)}")

    current = {"vLLM": {"jd_b"}}
    diff = diff_against_last(current, history[:-1])  # 对比第一次
    _assert("vLLM" in diff.added, "diff did not detect newly added gap")
    _assert("RAG" in diff.resolved, "diff did not detect resolved gap")
    rendered_html = render_career_trends_html(2, current, diff)
    _assert("vLLM" in rendered_html and "跨 JD 缺口趋势" in rendered_html, "career-trends HTML lost gap data")


def _assert_mastery_history_progression(temp_root: Path) -> None:
    """mastery 快照记录 + 时间线渲染 + progression 标记。"""
    # 模拟两次 mastery 快照：C → A
    from datetime import datetime, timezone
    from .mastery_history import MasterySnapshot

    snap1 = MasterySnapshot(
        date="2026-01-01T00:00:00+00:00",
        application="smoke_test_app",
        mastery={"fact_a": "C"},
    )
    snap2 = MasterySnapshot(
        date="2026-02-01T00:00:00+00:00",
        application="smoke_test_app",
        mastery={"fact_a": "A"},
    )
    history_path = temp_root / "data" / "mastery_history.json"
    import json as _json
    from dataclasses import asdict as _asdict
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        _json.dumps([_asdict(snap1), _asdict(snap2)], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    history = load_mastery_history(temp_root)
    _assert(len(history) == 2, f"expected 2 mastery snapshots, got {len(history)}")

    rendered = render_mastery_history(history, fact_id="fact_a")
    _assert("improved +2" in rendered, f"progression label not rendered: {rendered}")
    _assert("C -> A" in rendered, f"progression arrow not rendered: {rendered}")

    review_path = temp_root / "data" / "outputs" / "mastery_wire" / "review_sheet.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        """### Fact

- fact_id: `intern_data_automation`
- mastery_check: `A confirmed`
- confirmed_via: `interactive_cli`
""",
        encoding="utf-8",
    )
    record_mastery_snapshot(temp_root, "mastery_wire", review_path, timestamp="2026-03-01T00:00:00+00:00")
    record_mastery_snapshot(temp_root, "mastery_wire", review_path, timestamp="2026-03-02T00:00:00+00:00")
    wired_history = load_mastery_history(temp_root)
    matching = [item for item in wired_history if item.application == "mastery_wire"]
    _assert(len(matching) == 1, "identical mastery decision created duplicate snapshots")


def main() -> int:
    run_smoke()
    _assert_web_app_available()
    _run_agent_tests()
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

from .analyzer import AnalysisResult, FactMatch, merge_keyword_floor
from .cli import main as cli_main
from .decision_flow import run_interactive_decision
from .eval_matchers import score_matches
from .jd_insight import (
    BONUS_HEADING,
    HARD_REQUIREMENT_HEADING,
    _classify_not_writable,
    _lines_for,
    _sections_by_heading,
    build_jd_insight_data,
    llm_interview_followups,
    llm_phrasing_candidates,
)
from .llm_client import LLMNotConfigured, chat_completion, get_api_key
from .outcomes import default_outcome_path, load_outcomes
from .profile import load_profile
from .review_parser import count_pending_review_items, parse_review_mastery
from .review import render_review_sheet
from .rules import Fact, find_not_writable
from .semantic.guardrails import find_blocked_terms
from .status import inspect_application, render_status, status_stage


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_JD = PROJECT_ROOT / "data" / "sample_jds" / "tencent_ai_application.md"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _copy_required_data(temp_root: Path) -> None:
    for relative in ("data/facts", "data/profile", "data/resume_fragments", "data/semantic_index"):
        source = PROJECT_ROOT / relative
        target = temp_root / relative
        if source.exists():
            shutil.copytree(source, target)


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
) -> tuple[int, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr), patch("builtins.input", side_effect=side_effect):
        code = run_interactive_decision(review_path, project_root=project_root)
    return code, stdout.getvalue() + stderr.getvalue()


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
    _assert(review.count("Truthful Resume Agent：面向 JD 的真实经历匹配与 RAG 辅助工具") == 1, "composite project rendered more than once")
    _assert(review.count("- fact_id: `project_truthful_resume_agent_cli`") == 1, "CLI source fact missing")
    _assert(review.count("- fact_id: `project_truthful_resume_agent_rag_qdrant`") == 1, "RAG source fact missing")
    _assert(review.count("- mastery_check: `待确认`") == 1, "composite strong/weak split should ask once")


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
""",
        encoding="utf-8",
    )


def run_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="truthful_resume_smoke_") as raw_temp:
        temp_root = Path(raw_temp)
        _copy_required_data(temp_root)

        _assert_jd_insight_structural_extraction()
        _assert_jd_insight_not_writable_classification()
        _assert_not_writable_terms_are_evidence_checks()
        _assert_keyword_floor_merges_with_semantic_matches()
        _assert_matcher_metrics_count_false_positives()
        _assert_review_parser_supports_composite_fact_blocks(temp_root)
        _assert_review_renders_composite_across_match_levels()
        _assert_llm_phrasing_candidates_are_advisory_and_screened()
        _assert_llm_interview_followups_only_render_questions()
        _assert_llm_phrasing_boundary_screen_fails_closed()
        _assert_llm_not_configured_degrades(temp_root)
        _assert_llm_provider_is_configurable()
        _assert_status_rejects_stale_artifacts(temp_root)

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
        _assert("RAG" not in tex_text, "not-writable RAG leaked into generated resume")
        _assert("vector database" not in tex_text, "not-writable vector database leaked into generated resume")

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
        _assert("Gap candidates added: 3" in expand_output, "expand-review did not append all gap candidates")
        expanded_review = review_path.read_text(encoding="utf-8")
        _assert("## Gap Review Candidates" in expanded_review, "expand-review did not add review section")
        _assert("fact_id: `intern_data_automation`" in expanded_review, "expand-review missed data automation candidate")
        _assert(count_pending_review_items(review_path) == 3, "expand-review did not create pending review items")

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
            "面向 JD 的真实经历匹配与 RAG 辅助工具" in composite_tex,
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


def main() -> int:
    run_smoke()
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

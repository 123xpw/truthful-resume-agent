"""Fixed regression cases for deterministic JD requirement evidence."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from .job_analysis import build_job_analysis_preview, extract_requirements
from .rules import Fact
from .web.app import app


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


FACTS = (
    Fact(
        id="agent_api_fact",
        title="Agent API project",
        keywords=("Python", "FastAPI", "LangGraph", "tool calling"),
        summary="Built a Python FastAPI service around a LangGraph workflow with tool calling.",
        boundaries=("Local service; not production-grade or high-concurrency.",),
        risk="medium",
    ),
    Fact(
        id="rag_fact",
        title="Local RAG project",
        keywords=("RAG", "Qdrant"),
        summary="Built a local RAG retrieval workflow backed by Qdrant.",
        boundaries=("No MCP integration and no multi-user deployment.",),
        risk="high",
    ),
)


JD = """# 示例岗位

## 岗位职责
1. 负责维护 Agent 工作流。

## 职位要求
1. 熟悉 Python、FastAPI 与 LangGraph，能够实现 tool calling。
2. 具备高并发和生产级服务经验。
3. 具备支付风控经验。

## 加分项
- RAG 与 MCP 项目经验。
"""


def _row(payload: dict, text: str) -> dict:
    return next(item for item in payload["requirements"] if item["jd_text"] == text)


def test_markdown_requirements_keep_source_kind_and_text() -> None:
    requirements, warnings, structure = extract_requirements(JD)
    _assert(structure == "markdown_headings", "markdown heading structure was not detected")
    _assert(not warnings, "well-structured JD emitted an unexpected warning")
    _assert(len(requirements) == 5, "explicit requirement count changed")
    _assert(requirements[0].kind == "responsibility", "responsibility was misclassified")
    _assert(requirements[1].kind == "hard_requirement", "hard requirement was misclassified")
    _assert(requirements[-1].kind == "bonus", "bonus was misclassified")
    _assert(requirements[1].jd_text.startswith("熟悉 Python"), "JD source text was rewritten")


def test_evidence_matrix_is_per_requirement_and_traceable() -> None:
    payload = build_job_analysis_preview(JD, facts=FACTS)
    _assert("不等于候选人已经完整满足" in payload["interpretation"]["note"], "coverage limitation is missing")

    direct = _row(payload, "熟悉 Python、FastAPI 与 LangGraph，能够实现 tool calling。")
    _assert(direct["evidence_level"] == "direct_support", "explicit project evidence was not direct")
    _assert(direct["evidence"][0]["fact_id"] == "agent_api_fact", "direct evidence lacks the expected fact_id")
    _assert("FastAPI" in direct["evidence"][0]["matched_keywords"], "matched keywords are not exposed")
    _assert(direct["evidence"][0]["boundaries"], "fact boundaries are missing from evidence")

    unsupported = _row(payload, "具备支付风控经验。")
    _assert(unsupported["evidence_level"] == "no_evidence", "unrelated facts leaked across JD lines")
    _assert(unsupported["evidence"] == (), "no-evidence requirement unexpectedly cites a fact")


def test_strength_boundaries_and_mixed_technology_fail_closed() -> None:
    payload = build_job_analysis_preview(JD, facts=FACTS)

    scale = _row(payload, "具备高并发和生产级服务经验。")
    scale_terms = {item["term"] for item in scale["blocked_claims"]}
    _assert(scale["evidence_level"] == "not_writable", "unsupported operating scale was not blocked")
    _assert({"高并发", "生产级"}.issubset(scale_terms), "blocked scale claims are not explained")

    mixed = _row(payload, "RAG 与 MCP 项目经验。")
    _assert(mixed["evidence_level"] == "not_writable", "mixed supported/unsupported line did not fail closed")
    _assert(mixed["has_mixed_evidence"] is True, "mixed evidence is not explicit")
    _assert(any(item["fact_id"] == "rag_fact" for item in mixed["evidence"]), "supported RAG fact disappeared")
    _assert({item["term"] for item in mixed["blocked_claims"]} == {"MCP"}, "unsupported MCP was not isolated")


def test_unheaded_lists_are_not_guessed_into_hard_requirements() -> None:
    payload = build_job_analysis_preview("- Python 开发经验\n- 熟悉支付风控", facts=FACTS)
    _assert(payload["structure"] == "unclassified_list", "unheaded input structure is incorrect")
    _assert(all(item["kind"] == "unclassified" for item in payload["requirements"]), "category was guessed")
    _assert(payload["warnings"][0]["code"] == "JD_STRUCTURE_NOT_FOUND", "missing-structure warning absent")


def test_group_labels_are_not_presented_as_requirements() -> None:
    jd = """岗位要求：
1. 专业能力：
   - 熟悉 Python 和 FastAPI。
"""
    payload = build_job_analysis_preview(jd, facts=FACTS)
    _assert(payload["structure"] == "plain_headings", "plain heading was not recognized")
    _assert(payload["summary"]["total_requirements"] == 1, "group label became a fake requirement")
    _assert(payload["warnings"][0]["code"] == "GROUP_LABELS_IGNORED", "ignored label was not disclosed")


def test_preview_api_neither_saves_jd_nor_calls_legacy_analyzer() -> None:
    web_module = importlib.import_module("backend.resume_agent.web.app")
    original_save = web_module.save_jd_memory
    original_analyze = web_module.analyze_jd
    jd_library = Path(web_module.PROJECT_ROOT) / "data" / "jd_library"
    before = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in jd_library.glob("*.md")
    }

    def forbidden_save(*_args, **_kwargs):
        raise AssertionError("preview attempted to persist the JD")

    def forbidden_call(*_args, **_kwargs):
        raise AssertionError("preview attempted to call the legacy analyzer or LLM")

    web_module.save_jd_memory = forbidden_save
    web_module.analyze_jd = forbidden_call
    try:
        with patch("backend.resume_agent.llm_client.chat_completion", side_effect=forbidden_call):
            with TestClient(app) as client:
                metadata = client.get("/api/meta")
                response = client.post(
                    "/api/job-analysis/preview",
                    json={"jd_text": "## Requirements\n- Python and FastAPI development experience."},
                )
                empty = client.post("/api/job-analysis/preview", json={"jd_text": "   \n"})
    finally:
        web_module.save_jd_memory = original_save
        web_module.analyze_jd = original_analyze

    after = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in jd_library.glob("*.md")
    }
    _assert(response.status_code == 200, "preview API failed")
    _assert(
        metadata.json()["job_analysis_preview"]
        == {"path": "/api/job-analysis/preview", "saves_jd": False, "llm_calls": 0},
        "preview safety contract is not discoverable",
    )
    _assert(response.json()["saved"] is False, "preview response did not disclose non-persistence")
    _assert(response.json()["llm_calls"] == 0, "preview response did not disclose zero LLM calls")
    _assert(before == after, "preview changed the JD library")
    _assert(empty.status_code == 400 and empty.json()["detail"]["code"] == "EMPTY_JD", "blank JD did not fail clearly")


def test_job_analysis_page_is_separate_and_uses_preview_contract() -> None:
    with TestClient(app) as client:
        dashboard = client.get("/")
        page = client.get("/job-analysis")
    _assert(page.status_code == 200, "job-analysis page route failed")
    _assert("岗位匹配分析" in page.text and "要求与证据" in page.text, "job-analysis page content is incomplete")
    _assert("/api/job-analysis/preview" in page.text, "page does not use the preview endpoint")
    _assert("不保存本次 JD" in page.text and "不调用 LLM" in page.text, "preview boundary is not visible")
    _assert("payload.saved !== false || payload.llm_calls !== 0" in page.text, "client does not enforce preview safety")
    _assert('href="/job-analysis"' in dashboard.text, "dashboard has no entry to the separate module")


ALL_TESTS = [
    test_markdown_requirements_keep_source_kind_and_text,
    test_evidence_matrix_is_per_requirement_and_traceable,
    test_strength_boundaries_and_mixed_technology_fail_closed,
    test_unheaded_lists_are_not_guessed_into_hard_requirements,
    test_group_labels_are_not_presented_as_requirements,
    test_preview_api_neither_saves_jd_nor_calls_legacy_analyzer,
    test_job_analysis_page_is_separate_and_uses_preview_contract,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项岗位分析测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

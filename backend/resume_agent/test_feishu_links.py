"""Deterministic tests for local Feishu-to-application links."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from .feishu_links import (
    delete_feishu_application_link,
    list_feishu_application_links,
    save_feishu_application_link,
)
from .feishu_sync import FeishuSheetSnapshot, persist_feishu_snapshot
from .web.app import app, get_project_root


HEADERS = [
    "序号",
    "公司名称",
    "行业分类",
    "岗位名称",
    "投递时间",
    "笔试时间",
    "当前状态",
    "投递方式/内推码",
    "优先级",
    "下一步动作",
    "备注",
]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _project(temp_dir: str) -> Path:
    root = Path(temp_dir) / "truthful-resume-agent"
    (root / "data" / "jd_library").mkdir(parents=True)
    output = root / "data" / "outputs" / "demo_application"
    output.mkdir(parents=True)
    (root / "data" / "jd_library" / "demo_application.md").write_text("# Demo JD\n", encoding="utf-8")
    (output / "match_report.md").write_text("# Match\n", encoding="utf-8")
    (output / "resume_draft.pdf").write_bytes(b"%PDF-1.4\nfirst")
    return root


def _snapshot(company: str = "示例公司") -> FeishuSheetSnapshot:
    values = [
        HEADERS,
        [1, company, "AI", "Agent 工程师", "8.25", "", "简历筛选中", "官网", "高", "跟进", ""],
    ]
    import hashlib
    import json

    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return FeishuSheetSnapshot(
        document_url="https://example.feishu.cn/sheets/shtcnExampleToken1234567890",
        sheet_id="sheet01",
        sheet_title="投递台账",
        revision="1",
        values=values,
        content_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        row_count=2,
        column_count=11,
    )


def test_link_binds_row_application_and_pdf_hash() -> None:
    with TemporaryDirectory(prefix="resume-feishu-link-") as temp_dir:
        root = _project(temp_dir)
        persist_feishu_snapshot(root, _snapshot())
        link = save_feishu_application_link(
            root,
            sequence="1",
            application_name="demo_application",
            resume_ref="output:demo_application/resume_draft.pdf",
        )
        result = list_feishu_application_links(root)
        _assert(link["application_state"] == "prepare", "workflow state was not attached")
        _assert(link["resume_sha256_at_link"] == link["current_resume_sha256"], "PDF hash was not bound")
        _assert(not link["row_stale"] and not link["artifact_changed"], "fresh link was marked stale")
        _assert(result["linked_count"] == 1 and result["unlinked_count"] == 0, "link counts are incorrect")


def test_link_reports_canonical_pdf_mismatch_and_missing_application() -> None:
    with TemporaryDirectory(prefix="resume-feishu-link-canonical-") as temp_dir:
        root = _project(temp_dir)
        persist_feishu_snapshot(root, _snapshot())
        output = root / "data" / "outputs" / "demo_application"
        (output / "canonical_audit.json").write_text(
            '{"ready": true, "pdf_sha256": "not-the-current-pdf"}',
            encoding="utf-8",
        )
        save_feishu_application_link(
            root,
            sequence="1",
            application_name="demo_application",
            resume_ref="output:demo_application/resume_draft.pdf",
        )
        link = list_feishu_application_links(root)["links"][0]
        _assert(link["canonical_pdf_matches_current"] is False, "canonical/PDF mismatch was hidden")
        (root / "data" / "jd_library" / "demo_application.md").unlink()
        for child in output.iterdir():
            child.unlink()
        output.rmdir()
        link = list_feishu_application_links(root)["links"][0]
        _assert(link["application_state"] == "missing", "deleted application was not reported missing")


def test_link_detects_row_and_artifact_changes_then_archives() -> None:
    with TemporaryDirectory(prefix="resume-feishu-link-stale-") as temp_dir:
        root = _project(temp_dir)
        persist_feishu_snapshot(root, _snapshot())
        save_feishu_application_link(
            root,
            sequence="1",
            application_name="demo_application",
            resume_ref="output:demo_application/resume_draft.pdf",
        )
        persist_feishu_snapshot(root, _snapshot(company="修改后的公司"))
        (root / "data" / "outputs" / "demo_application" / "resume_draft.pdf").write_bytes(b"%PDF-1.4\nsecond")
        link = list_feishu_application_links(root)["links"][0]
        _assert(link["row_stale"], "changed ledger identity did not stale the link")
        _assert(link["artifact_changed"], "changed PDF did not stale the recorded hash")
        delete_feishu_application_link(root, "1")
        result = list_feishu_application_links(root)
        _assert(result["linked_count"] == 0 and result["unlinked_count"] == 1, "archived link remained active")


def test_link_rejects_unknown_sequence_and_application() -> None:
    with TemporaryDirectory(prefix="resume-feishu-link-invalid-") as temp_dir:
        root = _project(temp_dir)
        persist_feishu_snapshot(root, _snapshot())
        for sequence, application in (("2", "demo_application"), ("1", "missing_application")):
            try:
                save_feishu_application_link(root, sequence=sequence, application_name=application, resume_ref=None)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid Feishu link was accepted")


def test_link_api_contract_uses_no_llm() -> None:
    with TemporaryDirectory(prefix="resume-feishu-link-api-") as temp_dir:
        root = _project(temp_dir)
        persist_feishu_snapshot(root, _snapshot())
        app.dependency_overrides[get_project_root] = lambda: root
        try:
            with TestClient(app) as client:
                saved = client.put(
                    "/api/feishu-links/1",
                    json={"application": "demo_application", "resume_ref": "output:demo_application/resume_draft.pdf"},
                )
                _assert(saved.status_code == 200 and saved.json()["llm_calls"] == 0, "link API write failed")
                listed = client.get("/api/feishu-links")
                _assert(listed.status_code == 200 and listed.json()["linked_count"] == 1, "link API read failed")
                archived = client.delete("/api/feishu-links/1")
                _assert(archived.status_code == 200 and archived.json()["archived"], "link API archive failed")
        finally:
            app.dependency_overrides.clear()


ALL_TESTS = [
    test_link_binds_row_application_and_pdf_hash,
    test_link_reports_canonical_pdf_mismatch_and_missing_application,
    test_link_detects_row_and_artifact_changes_then_archives,
    test_link_rejects_unknown_sequence_and_application,
    test_link_api_contract_uses_no_llm,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项 Feishu application link 测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

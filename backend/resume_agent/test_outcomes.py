"""Deterministic tests for the local, zero-LLM application outcome tracker."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("RESUME_AGENT_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient

from .outcomes import (
    default_outcome_path,
    delete_outcome,
    list_resume_artifacts,
    load_outcomes,
    record_outcome,
    resolve_resume_ref,
    summarize_outcomes,
    update_outcome,
)
from .web.app import app, get_project_root


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _project(temp_dir: str) -> Path:
    root = Path(temp_dir) / "workspace" / "projects" / "truthful-resume-agent"
    (root / "data" / "outputs").mkdir(parents=True)
    return root


def _pdf(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"%PDF-1.4\n{marker}\n".encode("utf-8"))
    return path


def test_legacy_events_receive_stable_ids() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-") as temp_dir:
        root = _project(temp_dir)
        path = default_outcome_path(root)
        path.write_text(
            json.dumps(
                [
                    {
                        "application": "legacy_application",
                        "status": "applied",
                        "date": "2026-08-01",
                        "resume_sha256": None,
                        "resume_path": None,
                        "note": "legacy",
                    }
                ]
            ),
            encoding="utf-8",
        )
        first = load_outcomes(path)[0]
        second = load_outcomes(path)[0]
        _assert(first.event_id.startswith("legacy-"), "legacy event ID was not generated")
        _assert(first.event_id == second.event_id, "legacy event ID is not stable")


def test_configured_outcome_path_supports_container_persistence() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-path-") as temp_dir:
        root = _project(temp_dir)
        configured = Path(temp_dir) / "runtime" / "application_outcomes.json"
        with patch.dict(os.environ, {"RESUME_AGENT_OUTCOME_PATH": str(configured)}):
            _assert(default_outcome_path(root) == configured, "configured outcome path was ignored")


def test_record_update_delete_and_summary() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-") as temp_dir:
        root = _project(temp_dir)
        resume = _pdf(root / "data" / "outputs" / "demo" / "resume_draft.pdf", "demo")
        recorded = record_outcome(
            root,
            "demo",
            "applied",
            "2026-08-20",
            "submitted",
            resume_path=resume,
            use_default_resume=False,
        )
        _assert(recorded.resume_sha256 == hashlib.sha256(resume.read_bytes()).hexdigest(), "PDF hash mismatch")
        updated = update_outcome(
            root,
            recorded.event_id,
            "demo",
            "interview",
            "2026-08-22",
            "first round",
            resume_path=resume,
        )
        _assert(updated.status == "interview" and updated.event_id == recorded.event_id, "update failed")
        summary = summarize_outcomes(load_outcomes(default_outcome_path(root)))
        _assert(summary["tracked_applications"] == 1, "tracked application count mismatch")
        _assert(summary["ever_by_status"]["interview"] == 1, "interview summary mismatch")
        delete_outcome(root, recorded.event_id)
        _assert(load_outcomes(default_outcome_path(root)) == [], "delete failed")


def test_concurrent_records_are_not_lost() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-concurrent-") as temp_dir:
        root = _project(temp_dir)

        def record(index: int) -> None:
            record_outcome(
                root,
                f"application-{index}",
                "applied",
                "2026-08-24",
                use_default_resume=False,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(record, range(20)))

        events = load_outcomes(default_outcome_path(root))
        _assert(len(events) == 20, "concurrent outcome writes lost events")
        _assert(len({event.event_id for event in events}) == 20, "event IDs are not unique")


def test_artifact_refs_are_bounded_to_allowed_pdf_roots() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-") as temp_dir:
        root = _project(temp_dir)
        output_pdf = _pdf(root / "data" / "outputs" / "demo" / "resume_draft.pdf", "output")
        delivery_root = root.parent.parent / "投递版本"
        _pdf(delivery_root / "demo-company" / "未验证勿投递_demo.pdf", "unverified")
        artifacts = list_resume_artifacts(root)
        _assert(len(artifacts) == 2, "artifact scan count mismatch")
        _assert(any(item.state == "unverified" for item in artifacts), "unsafe filename state missing")
        resolved = resolve_resume_ref(root, "output:demo/resume_draft.pdf")
        _assert(resolved == output_pdf.resolve(), "valid output reference did not resolve")
        try:
            resolve_resume_ref(root, "output:../outside.pdf")
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal was accepted")


def test_outcome_api_contract_uses_no_llm() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-api-") as temp_dir:
        root = _project(temp_dir)
        _pdf(root / "data" / "outputs" / "demo" / "resume_draft.pdf", "api")
        app.dependency_overrides[get_project_root] = lambda: root
        try:
            with TestClient(app) as client:
                artifacts = client.get("/api/resume-artifacts")
                _assert(artifacts.status_code == 200 and artifacts.json()["llm_calls"] == 0, "artifact API failed")
                created = client.post(
                    "/api/outcomes",
                    json={
                        "application": "Demo Role",
                        "status": "applied",
                        "date": "2026-08-23",
                        "note": "submitted",
                        "resume_ref": "output:demo/resume_draft.pdf",
                    },
                )
                _assert(created.status_code == 201 and created.json()["llm_calls"] == 0, "create API failed")
                event_id = created.json()["event"]["event_id"]
                listed = client.get("/api/outcomes")
                _assert(listed.json()["summary"]["event_count"] == 1, "list API summary failed")
                updated = client.put(
                    f"/api/outcomes/{event_id}",
                    json={
                        "application": "Demo Role",
                        "status": "assessment",
                        "date": "2026-08-24",
                        "note": "assessment received",
                        "resume_ref": "output:demo/resume_draft.pdf",
                    },
                )
                _assert(updated.status_code == 200, "update API failed")
                _assert(updated.json()["event"]["status"] == "assessment", "updated status mismatch")
                invalid = client.post(
                    "/api/outcomes",
                    json={
                        "application": "bad",
                        "status": "applied",
                        "date": "2026-08-24",
                        "resume_ref": "output:../private.pdf",
                    },
                )
                _assert(invalid.status_code == 400, "invalid resume ref did not fail")
                deleted = client.delete(f"/api/outcomes/{event_id}")
                _assert(deleted.status_code == 200 and deleted.json()["deleted"], "delete API failed")
        finally:
            app.dependency_overrides.clear()


ALL_TESTS = [
    test_legacy_events_receive_stable_ids,
    test_configured_outcome_path_supports_container_persistence,
    test_record_update_delete_and_summary,
    test_concurrent_records_are_not_lost,
    test_artifact_refs_are_bounded_to_allowed_pdf_roots,
    test_outcome_api_contract_uses_no_llm,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项 outcome tracker 测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

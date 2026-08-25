"""Deterministic tests for the local, zero-LLM application outcome tracker."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("RESUME_AGENT_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient

from .outcomes import (
    create_outcome_backup,
    default_legacy_outcome_path,
    default_outcome_path,
    delete_outcome,
    export_outcomes,
    list_outcome_backups,
    list_resume_artifacts,
    load_outcomes,
    outcome_storage_info,
    record_outcome,
    resolve_resume_ref,
    restore_outcome,
    restore_outcome_backup,
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


def _default_store_environment():
    return patch.dict(
        os.environ,
        {
            "RESUME_AGENT_OUTCOME_PATH": "",
            "RESUME_AGENT_LEGACY_OUTCOME_PATH": "",
            "RESUME_AGENT_DATA_MODE": "preview",
        },
    )


def test_legacy_json_migrates_once_without_mutating_source() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-migrate-") as temp_dir, _default_store_environment():
        root = _project(temp_dir)
        legacy_path = default_legacy_outcome_path(root)
        original = json.dumps(
            [
                {
                    "application": "legacy_application",
                    "status": "applied",
                    "date": "2026-08-01",
                    "resume_sha256": None,
                    "resume_path": None,
                    "note": "legacy",
                }
            ],
            ensure_ascii=False,
        )
        legacy_path.write_text(original, encoding="utf-8")
        database_path = default_outcome_path(root)
        first = load_outcomes(database_path)
        second = load_outcomes(database_path)
        _assert(database_path.suffix == ".sqlite3" and database_path.exists(), "SQLite store was not created")
        _assert(len(first) == 1 and len(second) == 1, "legacy event was duplicated or lost")
        _assert(first[0].event_id.startswith("legacy-"), "legacy event ID was not generated")
        _assert(first[0].event_id == second[0].event_id, "legacy event ID is not stable")
        _assert(legacy_path.read_text(encoding="utf-8") == original, "legacy source was mutated")


def test_configured_outcome_path_and_mode_are_reported() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-path-") as temp_dir:
        root = _project(temp_dir)
        configured = Path(temp_dir) / "runtime" / "application_tracker.sqlite3"
        (root / ".env").write_text(
            f"RESUME_AGENT_OUTCOME_PATH={configured}\nRESUME_AGENT_DATA_MODE=pilot\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "RESUME_AGENT_OUTCOME_PATH": "",
                "RESUME_AGENT_DATA_MODE": "",
                "RESUME_AGENT_LEGACY_OUTCOME_PATH": "",
            },
        ):
            info = outcome_storage_info(root)
        _assert(info["database_path"] == str(configured.resolve()), "configured database path was ignored")
        _assert(info["mode"] == "pilot" and info["integrity"] == "ok", "storage status is incorrect")


def test_record_update_archive_restore_and_audit() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-") as temp_dir, _default_store_environment():
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
        _assert(load_outcomes(default_outcome_path(root)) == [], "archive remained active")
        archived = load_outcomes(default_outcome_path(root), include_archived=True)
        _assert(len(archived) == 1 and archived[0].archived_at is not None, "archive was not retained")
        restored = restore_outcome(root, recorded.event_id)
        _assert(restored.archived_at is None and len(load_outcomes(default_outcome_path(root))) == 1, "restore failed")
        with sqlite3.connect(default_outcome_path(root)) as connection:
            audit_rows = connection.execute(
                "SELECT action FROM outcome_event_audit ORDER BY audit_id"
            )
            actions = [row[0] for row in audit_rows]
            connection.executemany(
                """
                INSERT INTO outcome_events(
                    event_id, application, status, event_date, resume_sha256,
                    resume_path, note, created_at, updated_at, archived_at
                ) VALUES (?, ?, 'applied', ?, NULL, NULL, '', ?, ?, NULL)
                """,
                [
                    (
                        f"planner-{index}",
                        f"planner-application-{index}",
                        f"2026-07-{(index % 28) + 1:02d}",
                        "2026-08-25T00:00:00+00:00",
                        "2026-08-25T00:00:00+00:00",
                    )
                    for index in range(200)
                ],
            )
            connection.execute("ANALYZE")
            active_plan = " ".join(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM outcome_events "
                    "WHERE archived_at IS NULL ORDER BY event_date DESC"
                )
            )
            application_plan = " ".join(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM outcome_events "
                    "WHERE application = ? ORDER BY event_date",
                    ("demo",),
                )
            )
        _assert(actions == ["create", "update", "archive", "restore"], "audit trail is incomplete")
        _assert("idx_outcome_events_active_date" in active_plan, "active-date index is unused")
        _assert("idx_outcome_events_application_date" in application_plan, "application-date index is unused")
        _assert(len(list_outcome_backups(default_outcome_path(root))) == 4, "automatic backups are missing")


def test_backup_restore_and_export_round_trip() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-backup-") as temp_dir, _default_store_environment():
        root = _project(temp_dir)
        path = default_outcome_path(root)
        record_outcome(root, "first", "applied", "2026-08-20", use_default_resume=False)
        checkpoint = create_outcome_backup(path)
        record_outcome(root, "second", "interview", "2026-08-21", use_default_resume=False)
        _assert(len(load_outcomes(path)) == 2, "second event was not written")
        while len(list_outcome_backups(path)) < 10:
            create_outcome_backup(path)
        _assert(checkpoint.exists(), "restore checkpoint was pruned too early")
        safety_backup = restore_outcome_backup(path, checkpoint.name)
        restored = load_outcomes(path)
        _assert(len(restored) == 1 and restored[0].application == "first", "backup restore did not restore snapshot")
        _assert(safety_backup.exists(), "restore did not create a safety backup")
        json_text, json_type = export_outcomes(restored, "json")
        csv_text, csv_type = export_outcomes(restored, "csv")
        _assert(json.loads(json_text)[0]["application"] == "first", "JSON export failed")
        _assert("first" in csv_text and "application" in csv_text, "CSV export failed")
        _assert("json" in json_type and "csv" in csv_type, "export media type mismatch")


def test_concurrent_records_are_not_lost() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-concurrent-") as temp_dir, _default_store_environment():
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
        _assert(outcome_storage_info(root)["integrity"] == "ok", "database failed integrity check")


def test_legacy_json_path_remains_compatible() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-json-") as temp_dir, _default_store_environment():
        root = _project(temp_dir)
        path = Path(temp_dir) / "fixture" / "outcomes.json"
        event = record_outcome(
            root,
            "json-fixture",
            "applied",
            "2026-08-24",
            path=path,
            use_default_resume=False,
        )
        delete_outcome(root, event.event_id, path=path)
        _assert(load_outcomes(path) == [], "legacy JSON compatibility changed")


def test_artifact_refs_are_bounded_to_allowed_pdf_roots() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-") as temp_dir, _default_store_environment():
        root = _project(temp_dir)
        output_pdf = _pdf(root / "data" / "outputs" / "demo" / "resume_draft.pdf", "output")
        delivery_root = root.parent.parent / "投递版本"
        _pdf(delivery_root / "demo-company" / "未验证勿投递_demo.pdf", "unverified")
        artifacts = list_resume_artifacts(root)
        _assert(len(artifacts) == 2, "artifact scan count mismatch")
        _assert(any(item.state == "unverified" for item in artifacts), "unsafe filename state missing")
        _assert(all(item.application_hint and item.modified_at for item in artifacts), "artifact metadata missing")
        resolved = resolve_resume_ref(root, "output:demo/resume_draft.pdf")
        _assert(resolved == output_pdf.resolve(), "valid output reference did not resolve")
        try:
            resolve_resume_ref(root, "output:../outside.pdf")
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal was accepted")


def test_outcome_api_contract_uses_no_llm() -> None:
    with TemporaryDirectory(prefix="resume-outcomes-api-") as temp_dir, _default_store_environment():
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
                _assert(listed.json()["storage"]["backend"] == "sqlite", "storage status missing")
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
                exported = client.get("/api/outcomes/export?format=csv")
                _assert(exported.status_code == 200 and "assessment" in exported.text, "export API failed")
                backup = client.post("/api/outcomes/backups")
                _assert(backup.status_code == 201 and backup.json()["llm_calls"] == 0, "backup API failed")
                checkpoint_name = backup.json()["backup_name"]
                extra = client.post(
                    "/api/outcomes",
                    json={
                        "application": "temporary",
                        "status": "applied",
                        "date": "2026-08-24",
                    },
                )
                _assert(extra.status_code == 201, "temporary outcome for restore test failed")
                restored_backup = client.post(
                    f"/api/outcomes/backups/{checkpoint_name}/restore",
                    json={"confirm": "RESTORE"},
                )
                _assert(restored_backup.status_code == 200, "backup restore API failed")
                _assert(
                    client.get("/api/outcomes").json()["summary"]["event_count"] == 1,
                    "backup restore API restored the wrong snapshot",
                )
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
                archived = client.delete(f"/api/outcomes/{event_id}")
                _assert(archived.status_code == 200 and archived.json()["archived"], "archive API failed")
                listed_archived = client.get("/api/outcomes").json()
                _assert(len(listed_archived["events"]) == 0, "archived event remained active")
                _assert(len(listed_archived["archived_events"]) == 1, "archived event was not listed")
                restored = client.post(f"/api/outcomes/{event_id}/restore")
                _assert(restored.status_code == 200 and restored.json()["llm_calls"] == 0, "restore API failed")
        finally:
            app.dependency_overrides.clear()


ALL_TESTS = [
    test_legacy_json_migrates_once_without_mutating_source,
    test_configured_outcome_path_and_mode_are_reported,
    test_record_update_archive_restore_and_audit,
    test_backup_restore_and_export_round_trip,
    test_concurrent_records_are_not_lost,
    test_legacy_json_path_remains_compatible,
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

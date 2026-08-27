"""Deterministic tests for read-only Feishu spreadsheet synchronization."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from .feishu_analysis import analyze_feishu_values
from .feishu_sync import (
    FeishuSyncError,
    _clear_access_token_cache,
    _trim_empty_edges,
    feishu_sync_status,
    load_feishu_config,
    sync_feishu_sheet,
)
from .web.app import app, get_project_root


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _project(temp_dir: str) -> Path:
    root = Path(temp_dir) / "truthful-resume-agent"
    (root / "data").mkdir(parents=True)
    return root


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, values: list[list[object]] | None = None) -> None:
        self.values = values or [["公司", "职位", "状态"], ["示例公司", "Agent 工程师", "已投递"]]
        self.calls: list[tuple[str, str]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url))
        _assert(kwargs["json"]["app_secret"] == "local-secret", "app secret was not sent only to auth")
        return _Response({"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url))
        _assert(kwargs["headers"]["Authorization"] == "Bearer tenant-token", "bearer token missing")
        if url.endswith("/sheets/query"):
            return _Response(
                {
                    "code": 0,
                    "data": {
                        "sheets": [
                            {"sheet_id": "sheet01", "title": "投递台账", "hidden": False}
                        ]
                    },
                }
            )
        return _Response(
            {
                "code": 0,
                "data": {
                    "revision": 7,
                    "valueRange": {"revision": 7, "values": self.values},
                },
            }
        )


class _InvalidCredentialSession:
    def post(self, url: str, **kwargs):
        return _Response({"code": 10014, "msg": "app secret invalid"})


class _ExpiredTokenSession:
    def __init__(self) -> None:
        self.auth_calls = 0

    def post(self, url: str, **kwargs):
        self.auth_calls += 1
        return _Response(
            {
                "code": 0,
                "tenant_access_token": f"tenant-token-{self.auth_calls}",
                "expire": 7200,
            }
        )

    def get(self, url: str, **kwargs):
        token = kwargs["headers"]["Authorization"]
        if token == "Bearer tenant-token-1":
            return _Response({"code": 99991663, "msg": "invalid access token"})
        if url.endswith("/sheets/query"):
            return _Response(
                {"code": 0, "data": {"sheets": [{"sheet_id": "sheet01", "title": "投递台账"}]}}
            )
        return _Response({"code": 0, "data": {"valueRange": {"values": [["公司"], ["示例公司"]]}}})


def _environment() -> dict[str, str]:
    return {
        "RESUME_AGENT_FEISHU_SPREADSHEET_URL": "https://example.feishu.cn/sheets/shtcnExampleToken1234567890?from=from_copylink",
        "RESUME_AGENT_FEISHU_APP_ID": "cli_test",
        "RESUME_AGENT_FEISHU_APP_SECRET": "local-secret",
        "RESUME_AGENT_FEISHU_SHEET_ID": "",
        "RESUME_AGENT_FEISHU_RANGE": "A1:H100",
        "RESUME_AGENT_OUTCOME_PATH": "",
    }


def test_config_parses_copy_link_without_exposing_query() -> None:
    with TemporaryDirectory(prefix="resume-feishu-config-") as temp_dir, patch.dict(os.environ, _environment(), clear=False):
        config = load_feishu_config(_project(temp_dir))
        _assert(config.spreadsheet_token == "shtcnExampleToken1234567890", "spreadsheet token parsing failed")
        _assert(config.cell_range == "A1:H100", "configured range was ignored")


def test_config_rejects_lookalike_feishu_host() -> None:
    environment = _environment()
    environment["RESUME_AGENT_FEISHU_SPREADSHEET_URL"] = "https://evilfeishu.cn/sheets/shtcnExampleToken1234567890"
    with TemporaryDirectory(prefix="resume-feishu-host-") as temp_dir, patch.dict(os.environ, environment, clear=False):
        try:
            load_feishu_config(_project(temp_dir))
        except FeishuSyncError as exc:
            _assert(exc.code == "INVALID_FEISHU_URL", "lookalike host returned the wrong error")
        else:
            raise AssertionError("lookalike Feishu hostname was accepted")


def test_empty_range_edges_are_trimmed_without_losing_zeroes() -> None:
    values = [["公司", "状态", None, ""], ["示例", 0, None, ""], [None, "", None], []]
    _assert(_trim_empty_edges(values) == [["公司", "状态"], ["示例", 0]], "empty range edges were not trimmed")


def test_application_analysis_respects_status_semantics() -> None:
    values = [
        ["序号", "公司名称", "行业分类", "岗位名称", "投递时间", "笔试时间", "当前状态", "投递方式/内推码", "优先级", "下一步动作", "备注"],
        [1, "候选公司", "AI", "", "", "", "待投递", "官网", "高", "", ""],
        [2, "筛选公司", "AI", "", "8.24", "", "简历筛选中", "官网", "中", "", ""],
        [3, "笔试公司", "软件", "Agent", 46254, "8.26", "笔试阶段", "内推", "高", "完成笔试", ""],
        [4, "结束公司", "软件", "后端", "8.12", "", "已挂", "官网", "低", "", ""],
        ["", "新状态公司", "软件", "", "", "", "投递", "官网", "", "", ""],
    ]
    analysis = analyze_feishu_values(values)
    _assert(analysis["ready"] and analysis["total_records"] == 5, "analysis did not map the real ledger headers")
    _assert(analysis["planning_count"] == 1 and analysis["active_count"] == 2, "status groups are incorrect")
    _assert(analysis["closed_count"] == 1 and analysis["applied_count"] == 3, "pipeline counts are incorrect")
    _assert(analysis["missing_role_after_application"] == 1, "planning rows were incorrectly flagged for role")
    _assert(analysis["active_without_next_action"] == 1, "follow-up gap count is incorrect")
    _assert(len(analysis["application_date_styles"]) == 2, "mixed date styles were not detected")
    dashboard = analysis["dashboard"]
    _assert(
        dashboard["metrics"] == {"active": 2, "assessment": 1, "active_high_priority": 1, "planning": 1},
        "dashboard metrics do not follow the frozen product semantics",
    )
    _assert(dashboard["focus_items"][0]["sequence"] == "3", "assessment was not ranked first")
    _assert(dashboard["focus_items"][0]["action"] == "完成笔试", "candidate-authored action was ignored")
    _assert(
        {item["key"]: item["count"] for item in dashboard["stage_counts"]}
        == {"planning": 1, "screening": 1, "assessment": 1, "closed": 1, "other": 1},
        "dashboard stage mapping is incorrect",
    )


def test_missing_configuration_fails_without_network() -> None:
    with TemporaryDirectory(prefix="resume-feishu-missing-") as temp_dir, patch.dict(
        os.environ,
        {
            "RESUME_AGENT_FEISHU_SPREADSHEET_URL": "",
            "RESUME_AGENT_FEISHU_APP_ID": "",
            "RESUME_AGENT_FEISHU_APP_SECRET": "",
            "RESUME_AGENT_OUTCOME_PATH": "",
        },
        clear=False,
    ):
        root = _project(temp_dir)
        status = feishu_sync_status(root)
        _assert(not status["configured"] and not status["connected"], "missing config was reported as connected")
        try:
            sync_feishu_sheet(root, session=_Session())
        except FeishuSyncError as exc:
            _assert(exc.code == "FEISHU_NOT_CONFIGURED", "wrong missing-config error code")
        else:
            raise AssertionError("sync attempted without credentials")


def test_invalid_credentials_are_reported_explicitly() -> None:
    with TemporaryDirectory(prefix="resume-feishu-credentials-") as temp_dir, patch.dict(
        os.environ, _environment(), clear=False
    ):
        _clear_access_token_cache()
        try:
            sync_feishu_sheet(_project(temp_dir), session=_InvalidCredentialSession())
        except FeishuSyncError as exc:
            _assert(exc.code == "FEISHU_CREDENTIALS_INVALID", "invalid credentials returned a generic error")
            _assert(exc.provider_code == 10014 and not exc.retryable, "credential error metadata is incorrect")
        else:
            raise AssertionError("invalid credentials were accepted")


def test_sync_persists_versioned_snapshot_and_deduplicates() -> None:
    with TemporaryDirectory(prefix="resume-feishu-sync-") as temp_dir, patch.dict(os.environ, _environment(), clear=False):
        _clear_access_token_cache()
        root = _project(temp_dir)
        session = _Session()
        first = sync_feishu_sheet(root, session=session)
        second = sync_feishu_sheet(root, session=session)
        status = feishu_sync_status(root)
        _assert(first["changed"] is True and second["changed"] is False, "content hash did not deduplicate")
        _assert(status["connected"] and status["snapshot_count"] == 1, "snapshot state is incorrect")
        _assert(status["row_count"] == 2 and status["values"][1][0] == "示例公司", "snapshot values were lost")
        database_bytes = Path(first["database_path"]).read_bytes()
        _assert(b"local-secret" not in database_bytes, "app secret leaked into SQLite")
        _assert(any("sheets/query" in url for method, url in session.calls if method == "GET"), "sheet discovery was skipped")
        _assert(sum(method == "POST" for method, _ in session.calls) == 1, "valid access token was not reused")


def test_rejected_cached_token_refreshes_once() -> None:
    with TemporaryDirectory(prefix="resume-feishu-refresh-") as temp_dir, patch.dict(
        os.environ, _environment(), clear=False
    ):
        _clear_access_token_cache()
        session = _ExpiredTokenSession()
        result = sync_feishu_sheet(_project(temp_dir), session=session)
        _assert(result["row_count"] == 2, "sync did not recover after token refresh")
        _assert(session.auth_calls == 2, "rejected token was not refreshed exactly once")


def test_api_reports_missing_authorization_cleanly() -> None:
    with TemporaryDirectory(prefix="resume-feishu-api-") as temp_dir, patch.dict(
        os.environ,
        {
            "RESUME_AGENT_FEISHU_SPREADSHEET_URL": "",
            "RESUME_AGENT_FEISHU_APP_ID": "",
            "RESUME_AGENT_FEISHU_APP_SECRET": "",
            "RESUME_AGENT_OUTCOME_PATH": "",
        },
        clear=False,
    ):
        root = _project(temp_dir)
        app.dependency_overrides[get_project_root] = lambda: root
        try:
            with TestClient(app) as client:
                status = client.get("/api/feishu-sync")
                _assert(status.status_code == 200 and status.json()["llm_calls"] == 0, "status endpoint failed")
                sync = client.post("/api/feishu-sync")
                _assert(sync.status_code == 503, "missing authorization did not fail explicitly")
                _assert(sync.json()["detail"]["code"] == "FEISHU_NOT_CONFIGURED", "wrong API error code")
        finally:
            app.dependency_overrides.clear()


ALL_TESTS = [
    test_config_parses_copy_link_without_exposing_query,
    test_config_rejects_lookalike_feishu_host,
    test_empty_range_edges_are_trimmed_without_losing_zeroes,
    test_application_analysis_respects_status_semantics,
    test_missing_configuration_fails_without_network,
    test_invalid_credentials_are_reported_explicitly,
    test_sync_persists_versioned_snapshot_and_deduplicates,
    test_rejected_cached_token_refreshes_once,
    test_api_reports_missing_authorization_cleanly,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项 Feishu sync 测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

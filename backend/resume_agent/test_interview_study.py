"""Deterministic tests for the private Markdown interview study module."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from .interview_study import (
    build_study_payload,
    load_study_progress,
    parse_study_markdown,
    save_study_progress,
)
from .web.app import app, get_project_root


MARKDOWN = """# Private handbook

Preamble is not a study card.

## 1. Agent 基础

主题介绍。

### Agent 是什么？

模型在受限工具集中选择下一步动作。

### Tool Calling

```text
schema -> model call -> host dispatch -> result
```

## 2. RAG

- 检索
- 生成
"""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _private_root(temp_dir: str) -> Path:
    root = Path(temp_dir)
    handbook = root / "docs" / "handover_resume_interview_study.md"
    handbook.parent.mkdir(parents=True, exist_ok=True)
    handbook.write_text(MARKDOWN, encoding="utf-8")
    return root


def test_markdown_parser_builds_stable_topic_and_card_contract() -> None:
    first = parse_study_markdown(MARKDOWN)
    second = parse_study_markdown(MARKDOWN)
    _assert([topic.title for topic in first] == ["Agent 基础", "RAG"], "numbered headings were not normalized")
    _assert(len(first[0].cards) == 2 and first[1].cards[0].title == "核心内容", "card grouping changed")
    _assert(first[0].intro_markdown == "主题介绍。", "topic intro was lost")
    _assert(first[0].cards[0].card_id == second[0].cards[0].card_id, "card IDs are not deterministic")
    _assert("schema -> model call" in first[0].cards[1].body_markdown, "code block was not preserved")


def test_missing_private_handbook_uses_a_public_example() -> None:
    with TemporaryDirectory(prefix="interview-study-public-") as temp_dir:
        payload = build_study_payload(Path(temp_dir))
    _assert(payload["source"]["configured"] is False, "missing private handbook was presented as configured")
    _assert(payload["source"]["mode"] == "example", "public fallback mode is unclear")
    _assert(payload["summary"]["card_count"] >= 3, "public example has no useful cards")
    _assert(payload["llm_calls"] == 0, "study loader unexpectedly reports an LLM call")


def test_progress_is_local_persistent_and_incremental() -> None:
    with TemporaryDirectory(prefix="interview-study-progress-") as temp_dir:
        database = Path(temp_dir) / "progress.sqlite3"
        first = save_study_progress(database, "card-stable", "fuzzy")
        second = save_study_progress(database, "card-stable", "ready")
        loaded = load_study_progress(database, {"card-stable", "card-unseen"})
    _assert(first["review_count"] == 1, "first review count is incorrect")
    _assert(second["status"] == "ready" and second["review_count"] == 2, "progress update was not persisted")
    _assert(loaded["card-stable"]["status"] == "ready", "persisted progress did not reload")
    _assert(loaded["card-unseen"]["status"] == "unseen", "unknown card did not receive the default state")


def test_study_api_reads_private_markdown_and_saves_progress_without_llm() -> None:
    with TemporaryDirectory(prefix="interview-study-api-") as temp_dir:
        root = _private_root(temp_dir)
        app.dependency_overrides[get_project_root] = lambda: root
        try:
            with TestClient(app) as client:
                page = client.get("/interview-study")
                response = client.get("/api/interview-study")
                payload = response.json()
                card_id = payload["topics"][0]["cards"][0]["card_id"]
                saved = client.put(
                    f"/api/interview-study/progress/{card_id}",
                    json={"status": "mastered"},
                )
                reloaded = client.get("/api/interview-study").json()
                missing = client.put(
                    "/api/interview-study/progress/card-missing",
                    json={"status": "fuzzy"},
                )
                invalid = client.put(
                    f"/api/interview-study/progress/{card_id}",
                    json={"status": "unknown"},
                )
        finally:
            app.dependency_overrides.clear()
    _assert(page.status_code == 200 and "简历技术答辩训练" in page.text, "study page route failed")
    _assert(response.status_code == 200 and payload["source"]["configured"] is True, "private handbook did not load")
    _assert(payload["source"]["filename"] == "handover_resume_interview_study.md", "source path leaked or changed")
    _assert(saved.status_code == 200 and saved.json()["llm_calls"] == 0, "progress write contract failed")
    _assert(reloaded["topics"][0]["cards"][0]["progress"]["status"] == "mastered", "API did not reload progress")
    _assert(missing.status_code == 404, "unknown card was accepted")
    _assert(invalid.status_code == 422, "invalid status was accepted")


ALL_TESTS = [
    test_markdown_parser_builds_stable_topic_and_card_contract,
    test_missing_private_handbook_uses_a_public_example,
    test_progress_is_local_persistent_and_incremental,
    test_study_api_reads_private_markdown_and_saves_progress_without_llm,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项面试复习测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

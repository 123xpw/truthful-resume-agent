"""Agent 模块的自动化测试（不依赖 TTY，覆盖确定性部分 + 图编译）。

运行：python -m backend.resume_agent.agent.test_agent
"""

from __future__ import annotations

from . import memory
from .graph import build_agent
from .tools import search_facts, verify_fact


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_search_facts_hit() -> None:
    result = search_facts.invoke({"query": "RAG 向量检索 Qdrant"})
    _assert("project_truthful_resume_agent_rag_qdrant" in result, "search_facts 未命中 RAG 事实")
    _assert("边界" in result, "search_facts 未返回边界")


def test_search_facts_miss() -> None:
    result = search_facts.invoke({"query": "zzz 不存在的技术词 xyzabc"})
    _assert("没有" in result, "search_facts 空结果提示缺失")


def test_verify_fact_found() -> None:
    result = verify_fact.invoke({"fact_id": "intern_data_automation"})
    _assert("存在" in result and "风险等级" in result, "verify_fact 未返回风险等级")


def test_verify_fact_missing() -> None:
    result = verify_fact.invoke({"fact_id": "nonexistent_fact_xyz"})
    _assert("不存在" in result, "verify_fact 未识别不存在的事实")


def test_memory_roundtrip() -> None:
    memory.save_preference("_test_key", "_test_value")
    try:
        _assert(memory.recall_preference("_test_key") == "_test_value", "长期记忆写入/召回失败")
        _assert("_test_key" in memory.list_preferences(), "长期记忆列表失败")
    finally:
        data = memory.list_preferences()
        data.pop("_test_key", None)
        memory._save(data)


def test_graph_compiles() -> None:
    agent = build_agent()
    _assert(agent is not None, "Agent 图构建失败")


ALL_TESTS = [
    test_search_facts_hit,
    test_search_facts_miss,
    test_verify_fact_found,
    test_verify_fact_missing,
    test_memory_roundtrip,
    test_graph_compiles,
]


def run_all() -> None:
    for test in ALL_TESTS:
        test()


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Agent 模块的自动化测试（不依赖 TTY，覆盖确定性部分 + 图编译）。

运行：python -m backend.resume_agent.agent.test_agent
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage, HumanMessage

from . import memory
from . import graph
from . import prompts
from .graph import _parse_verifier_response, build_agent
from .tools import search_facts, verify_fact


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_search_facts_hit() -> None:
    result = json.loads(search_facts.invoke({"query": "RAG 向量检索 Qdrant"}))
    ids = {item["fact_id"] for item in result["matches"]}
    _assert("project_truthful_resume_agent_rag_qdrant" in ids, "search_facts 未命中 RAG 事实")
    _assert(all("boundaries" in item for item in result["matches"]), "search_facts 未返回边界")


def test_search_facts_miss() -> None:
    result = json.loads(search_facts.invoke({"query": "zzz 不存在的技术词 xyzabc"}))
    _assert(result["matches"] == [], "search_facts 空结果仍返回事实")
    _assert("没有" in result["message"], "search_facts 空结果提示缺失")


def test_verify_fact_found() -> None:
    result = json.loads(
        verify_fact.invoke({"fact_id": "intern_data_automation", "claim": "使用 Python 拉取数据"})
    )
    _assert(result["exists"] is True and result["risk"], "verify_fact 未返回完整事实")
    _assert(result["claim"] == "使用 Python 拉取数据", "verify_fact 未保留待校验 claim")


def test_verify_fact_missing() -> None:
    result = json.loads(verify_fact.invoke({"fact_id": "nonexistent_fact_xyz"}))
    _assert(result["exists"] is False, "verify_fact 未识别不存在的事实")


def test_verifier_parser_fails_closed() -> None:
    passed, _ = _parse_verifier_response("PASS")
    _assert(not passed, "非 JSON verifier 输出不应通过")
    passed, _ = _parse_verifier_response('{"status":"PASS","unsupported_claims":["仍有问题"]}')
    _assert(not passed, "带 unsupported_claims 的 PASS 不应通过")
    passed, feedback = _parse_verifier_response('{"status":"FAIL","unsupported_claims":["数字无依据"]}')
    _assert(not passed and "数字无依据" in feedback, "FAIL 原因未保留")


def test_graph_verifier_receives_evidence_and_retries() -> None:
    class FakeLLM:
        def __init__(self) -> None:
            self.counts = {"retrieve": 0, "generate": 0, "verify": 0, "reflect": 0}
            self.verify_inputs: list[str] = []

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            system = str(messages[0].content)
            if prompts.RETRIEVE_INSTRUCTION in system:
                self.counts["retrieve"] += 1
                # Deliberately skip tool calling: graph must obtain evidence itself.
                return AIMessage(content="未调用工具")
            if prompts.GENERATE_INSTRUCTION in system:
                self.counts["generate"] += 1
                return AIMessage(content=f"使用 Python 完成数据自动化 [intern_data_automation] v{self.counts['generate']}")
            if prompts.VERIFY_INSTRUCTION in system:
                self.counts["verify"] += 1
                self.verify_inputs.append(str(messages[-1].content))
                if self.counts["verify"] == 1:
                    return AIMessage(content='{"status":"FAIL","unsupported_claims":["v1 是无依据版本号"]}')
                return AIMessage(content='{"status":"PASS","unsupported_claims":[]}')
            if prompts.REFLECT_INSTRUCTION in system:
                self.counts["reflect"] += 1
                return AIMessage(content="删除无依据版本号")
            raise AssertionError("unexpected Agent node")

    fake = FakeLLM()
    original_llm = graph._llm
    graph._llm = fake
    try:
        result = build_agent().invoke(
            {"messages": [HumanMessage("介绍 Python 数据自动化经历")]},
            config={"configurable": {"thread_id": "grounding-test"}},
        )
    finally:
        graph._llm = original_llm

    _assert(result["verify_pass"] is True, "Agent 未在修正后通过校验")
    _assert(fake.counts == {"retrieve": 2, "generate": 2, "verify": 2, "reflect": 1}, "重试路径不完整")
    _assert(
        all("intern_data_automation" in item and "事实证据包" in item for item in fake.verify_inputs),
        "verifier 未收到事实编号与证据包",
    )


def test_memory_roundtrip() -> None:
    original_path = memory.MEMORY_PATH
    with TemporaryDirectory(prefix="truthful-resume-agent-memory-") as temp_dir:
        memory.MEMORY_PATH = Path(temp_dir) / "agent_memory.json"
        try:
            memory.save_preference("_test_key", "_test_value")
            _assert(memory.recall_preference("_test_key") == "_test_value", "长期记忆写入/召回失败")
            _assert("_test_key" in memory.list_preferences(), "长期记忆列表失败")
            deleted = memory.delete_preference("_test_key")
            _assert(deleted, "delete_preference 应返回 True")
            _assert(memory.recall_preference("_test_key") is None, "删除后 recall 应返回 None")
        finally:
            memory.MEMORY_PATH = original_path


def test_graph_compiles() -> None:
    agent = build_agent()
    _assert(agent is not None, "Agent 图构建失败")


ALL_TESTS = [
    test_search_facts_hit,
    test_search_facts_miss,
    test_verify_fact_found,
    test_verify_fact_missing,
    test_verifier_parser_fails_closed,
    test_graph_verifier_receives_evidence_and_retries,
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

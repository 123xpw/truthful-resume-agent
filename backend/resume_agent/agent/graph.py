"""LangGraph 状态图：检索 → 生成 → 校验 → 反思 闭环。"""

from __future__ import annotations

import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ..llm_client import get_api_key, get_api_url, get_model
from . import memory, prompts
from .tools import search_facts, verify_fact

MAX_TURNS = 3


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    draft: str
    evidence: str
    evidence_fact_ids: list[str]
    verification_feedback: str
    verify_pass: bool
    turn: int


_llm: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=get_model(),
            api_key=get_api_key(),
            base_url=get_api_url().removesuffix("/chat/completions"),
            temperature=0.2,
        )
    return _llm


def _execute_tool_calls(resp: AIMessage) -> tuple[list[ToolMessage], list[str], set[str]]:
    """执行 LLM 工具调用，同时保留供 verifier 使用的原始证据。"""
    results: list[ToolMessage] = []
    evidence: list[str] = []
    called_tools: set[str] = set()
    for call in resp.tool_calls:
        name = call.get("name") if isinstance(call, dict) else call.name
        args = call.get("args", {}) if isinstance(call, dict) else call.args
        call_id = call.get("id") if isinstance(call, dict) else call.id
        called_tools.add(name)
        if name == "search_facts":
            content = str(search_facts.invoke(args))
        elif name == "verify_fact":
            content = str(verify_fact.invoke(args))
        else:
            content = f"未知工具 {name}"
        results.append(ToolMessage(content=content, tool_call_id=call_id))
        if name in {"search_facts", "verify_fact"}:
            evidence.append(content)
    return results, evidence, called_tools


def _latest_user_query(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _evidence_fact_ids(evidence: list[str]) -> list[str]:
    ids: set[str] = set()
    for raw in evidence:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            fact_id = payload.get("fact_id")
            if payload.get("exists") is True and isinstance(fact_id, str):
                ids.add(fact_id)
            matches = payload.get("matches", [])
            if isinstance(matches, list):
                ids.update(
                    item["fact_id"]
                    for item in matches
                    if isinstance(item, dict) and isinstance(item.get("fact_id"), str)
                )
    return sorted(ids)


def _parse_verifier_response(content: str) -> tuple[bool, str]:
    """Parse strict verifier JSON; malformed output fails closed."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return False, f"verifier returned invalid JSON: {content.strip()}"
    if not isinstance(payload, dict):
        return False, "verifier response is not an object"
    status = payload.get("status")
    unsupported = payload.get("unsupported_claims")
    if status not in {"PASS", "FAIL"} or not isinstance(unsupported, list):
        return False, "verifier response has an invalid schema"
    if status == "PASS" and unsupported:
        return False, "verifier marked PASS while reporting unsupported claims"
    feedback = "; ".join(str(item) for item in unsupported) if unsupported else ""
    return status == "PASS", feedback


def _retrieve(state: AgentState) -> dict:
    llm = _get_llm().bind_tools([search_facts, verify_fact])
    messages = [SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.RETRIEVE_INSTRUCTION), *state["messages"]]
    resp = llm.invoke(messages)
    tool_messages, evidence, called_tools = _execute_tool_calls(resp)
    if "search_facts" not in called_tools:
        # Tool choice is not trusted as a safety boundary. Always obtain an
        # evidence bundle even when the model skips the requested tool call.
        evidence.append(str(search_facts.invoke({"query": _latest_user_query(state["messages"])})))
    return {
        "messages": [resp, *tool_messages],
        "evidence": "\n".join(evidence),
        "evidence_fact_ids": _evidence_fact_ids(evidence),
    }


def _generate(state: AgentState) -> dict:
    llm = _get_llm()
    prefs = memory.list_preferences()
    prefs_text = f"\n\n用户的长期偏好（跨会话记忆）：{prefs}" if prefs else ""
    evidence = state.get("evidence", "") or '{"matches":[],"message":"事实库中没有相关证据。"}'
    feedback = state.get("verification_feedback", "")
    retry_text = f"\n上一轮校验问题：{feedback}" if feedback else ""
    system = (
        prompts.SYSTEM_PROMPT
        + "\n"
        + prompts.GENERATE_INSTRUCTION
        + prefs_text
        + retry_text
        + f"\n\n本轮唯一允许使用的事实证据包：\n{evidence}"
    )
    messages = [SystemMessage(system), *state["messages"]]
    resp = llm.invoke(messages)
    return {"messages": [resp], "draft": str(resp.content)}


def _verify(state: AgentState) -> dict:
    llm = _get_llm()
    messages = [
        SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.VERIFY_INSTRUCTION),
        HumanMessage(
            "允许引用的 fact_id："
            + json.dumps(state.get("evidence_fact_ids", []), ensure_ascii=False)
            + "\n事实证据包：\n"
            + (state.get("evidence", "") or "<empty>")
            + "\n\n待校验的回答：\n"
            + state.get("draft", "")
        ),
    ]
    resp = llm.invoke(messages)
    verify_pass, feedback = _parse_verifier_response(str(resp.content))
    return {
        "verify_pass": verify_pass,
        "verification_feedback": feedback,
        "messages": [resp],
    }


def _reflect(state: AgentState) -> dict:
    llm = _get_llm()
    messages = [
        SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.REFLECT_INSTRUCTION),
        HumanMessage(
            "上一轮回答：\n"
            + state.get("draft", "")
            + "\n\n校验问题：\n"
            + (state.get("verification_feedback", "") or "verifier 未提供具体原因")
            + "\n\n本轮证据包：\n"
            + (state.get("evidence", "") or "<empty>")
        ),
    ]
    resp = llm.invoke(messages)
    return {"turn": state.get("turn", 0) + 1, "messages": [resp]}


def _route_after_verify(state: AgentState) -> str:
    if state.get("verify_pass"):
        return "end"
    if state.get("turn", 0) < MAX_TURNS:
        return "reflect"
    return "end"


def build_agent():
    """构建并编译 Agent 图（含短期记忆 checkpoint）。"""
    graph = StateGraph(AgentState)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("generate", _generate)
    graph.add_node("verify", _verify)
    graph.add_node("reflect", _reflect)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "verify")
    graph.add_conditional_edges("verify", _route_after_verify, {"end": END, "reflect": "reflect"})
    graph.add_edge("reflect", "retrieve")
    return graph.compile(checkpointer=MemorySaver())

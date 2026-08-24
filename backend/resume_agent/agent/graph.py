"""LangGraph 状态图：检索 → 生成 → 校验 → 反思 闭环。"""

from __future__ import annotations

import json
import re
from typing import Annotated, Callable, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ..llm_client import (
    LLMNotConfigured,
    LLMServiceError,
    call_with_retry,
    get_api_key,
    get_api_url,
    get_model,
    get_timeout_seconds,
)
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
    degraded: bool
    error_code: str
    error_message: str
    error_retryable: bool


_llm: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=get_model(),
            api_key=get_api_key(),
            base_url=get_api_url().removesuffix("/chat/completions"),
            temperature=0.2,
            timeout=get_timeout_seconds(),
            max_retries=0,
        )
    return _llm


def _llm_error(exc: Exception) -> dict:
    if isinstance(exc, LLMNotConfigured):
        return {
            "error_code": "LLM_NOT_CONFIGURED",
            "error_message": "LLM API key is not configured.",
            "error_retryable": False,
        }
    if isinstance(exc, LLMServiceError):
        return {
            "error_code": exc.code,
            "error_message": str(exc),
            "error_retryable": exc.retryable,
        }
    return {
        "error_code": "AGENT_NODE_FAILED",
        "error_message": "Agent node failed.",
        "error_retryable": False,
    }


def _invoke_llm(llm, messages: list) -> AIMessage:
    return call_with_retry(lambda: llm.invoke(messages))


def _execute_tool_calls(
    resp: AIMessage,
) -> tuple[list[ToolMessage], list[str], set[str], dict | None]:
    """执行 LLM 工具调用，同时保留供 verifier 使用的原始证据。"""
    results: list[ToolMessage] = []
    evidence: list[str] = []
    called_tools: set[str] = set()
    error: dict | None = None
    for call in resp.tool_calls:
        name = call.get("name") if isinstance(call, dict) else call.name
        args = call.get("args", {}) if isinstance(call, dict) else call.args
        call_id = call.get("id") if isinstance(call, dict) else call.id
        called_tools.add(name)
        try:
            if name == "search_facts":
                content = str(search_facts.invoke(args))
            elif name == "verify_fact":
                content = str(verify_fact.invoke(args))
            else:
                content = json.dumps({"error": "unknown_tool", "tool": str(name)})
                error = {
                    "error_code": "UNKNOWN_TOOL",
                    "error_message": "Agent requested an unknown tool.",
                    "error_retryable": False,
                }
        except Exception:
            content = json.dumps({"error": "tool_unavailable", "tool": str(name)})
            error = {
                "error_code": "FACT_TOOL_UNAVAILABLE",
                "error_message": "Fact tool is unavailable; generation was blocked.",
                "error_retryable": True,
            }
        results.append(ToolMessage(content=content, tool_call_id=call_id))
        if name in {"search_facts", "verify_fact"} and error is None:
            evidence.append(content)
    return results, evidence, called_tools, error


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


def _retrieve(state: AgentState, llm_provider: Callable = _get_llm) -> dict:
    try:
        llm = llm_provider().bind_tools([search_facts, verify_fact])
    except Exception as exc:
        return _llm_error(exc)
    messages = [SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.RETRIEVE_INSTRUCTION), *state["messages"]]
    try:
        resp = _invoke_llm(llm, messages)
    except Exception as exc:
        return _llm_error(exc)
    tool_messages, evidence, called_tools, tool_error = _execute_tool_calls(resp)
    if tool_error is not None:
        return {"messages": [resp, *tool_messages], **tool_error}
    if "search_facts" not in called_tools:
        # Tool choice is not trusted as a safety boundary. Always obtain an
        # evidence bundle even when the model skips the requested tool call.
        try:
            evidence.append(str(search_facts.invoke({"query": _latest_user_query(state["messages"])})))
        except Exception:
            return {
                "messages": [resp, *tool_messages],
                "error_code": "FACT_TOOL_UNAVAILABLE",
                "error_message": "Fact tool is unavailable; generation was blocked.",
                "error_retryable": True,
            }
    return {
        "messages": [resp, *tool_messages],
        "evidence": "\n".join(evidence),
        "evidence_fact_ids": _evidence_fact_ids(evidence),
    }


def _generate(state: AgentState, llm_provider: Callable = _get_llm) -> dict:
    try:
        llm = llm_provider()
    except Exception as exc:
        return _llm_error(exc)
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
    try:
        resp = _invoke_llm(llm, messages)
    except Exception as exc:
        return _llm_error(exc)
    return {"messages": [resp], "draft": str(resp.content)}


def _verify(state: AgentState, llm_provider: Callable = _get_llm) -> dict:
    try:
        llm = llm_provider()
    except Exception as exc:
        return _llm_error(exc)
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
    try:
        resp = _invoke_llm(llm, messages)
    except Exception as exc:
        return _llm_error(exc)
    verify_pass, feedback = _parse_verifier_response(str(resp.content))
    return {
        "verify_pass": verify_pass,
        "verification_feedback": feedback,
        "messages": [resp],
    }


def _reflect(state: AgentState, llm_provider: Callable = _get_llm) -> dict:
    try:
        llm = llm_provider()
    except Exception as exc:
        return _llm_error(exc)
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
    try:
        resp = _invoke_llm(llm, messages)
    except Exception as exc:
        return _llm_error(exc)
    return {"turn": state.get("turn", 0) + 1, "messages": [resp]}


def _route_after_step(state: AgentState) -> str:
    return "end" if state.get("error_code") else "continue"


def _route_after_verify(state: AgentState) -> str:
    if state.get("error_code"):
        return "end"
    if state.get("verify_pass"):
        return "end"
    if state.get("turn", 0) < MAX_TURNS:
        return "reflect"
    return "end"


def build_agent(
    checkpointer: BaseCheckpointSaver | None = None,
    llm=None,
):
    """构建并编译 Agent 图（含短期记忆 checkpoint）。"""
    llm_provider = (lambda: llm) if llm is not None else _get_llm
    graph = StateGraph(AgentState)
    graph.add_node("retrieve", lambda state: _retrieve(state, llm_provider))
    graph.add_node("generate", lambda state: _generate(state, llm_provider))
    graph.add_node("verify", lambda state: _verify(state, llm_provider))
    graph.add_node("reflect", lambda state: _reflect(state, llm_provider))
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", _route_after_step, {"continue": "generate", "end": END})
    graph.add_conditional_edges("generate", _route_after_step, {"continue": "verify", "end": END})
    graph.add_conditional_edges("verify", _route_after_verify, {"end": END, "reflect": "reflect"})
    graph.add_conditional_edges("reflect", _route_after_step, {"continue": "retrieve", "end": END})
    return graph.compile(checkpointer=checkpointer or MemorySaver())

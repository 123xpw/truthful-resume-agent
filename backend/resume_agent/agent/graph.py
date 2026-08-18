"""LangGraph 状态图：检索 → 生成 → 校验 → 反思 闭环。"""

from __future__ import annotations

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


def _execute_tool_calls(resp: AIMessage) -> list[ToolMessage]:
    """执行 LLM 发出的工具调用，返回 ToolMessage 列表。"""
    results: list[ToolMessage] = []
    for call in resp.tool_calls:
        name = call.get("name") if isinstance(call, dict) else call.name
        args = call.get("args", {}) if isinstance(call, dict) else call.args
        call_id = call.get("id") if isinstance(call, dict) else call.id
        if name == "search_facts":
            content = str(search_facts.invoke(args))
        elif name == "verify_fact":
            content = str(verify_fact.invoke(args))
        else:
            content = f"未知工具 {name}"
        results.append(ToolMessage(content=content, tool_call_id=call_id))
    return results


def _retrieve(state: AgentState) -> dict:
    llm = _get_llm().bind_tools([search_facts, verify_fact])
    messages = [SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.RETRIEVE_INSTRUCTION), *state["messages"]]
    resp = llm.invoke(messages)
    tool_messages = _execute_tool_calls(resp)
    return {"messages": [resp, *tool_messages]}


def _generate(state: AgentState) -> dict:
    llm = _get_llm()
    prefs = memory.list_preferences()
    prefs_text = f"\n\n用户的长期偏好（跨会话记忆）：{prefs}" if prefs else ""
    system = prompts.SYSTEM_PROMPT + "\n" + prompts.GENERATE_INSTRUCTION + prefs_text
    messages = [SystemMessage(system), *state["messages"]]
    resp = llm.invoke(messages)
    return {"messages": [resp], "draft": str(resp.content)}


def _verify(state: AgentState) -> dict:
    llm = _get_llm()
    messages = [
        SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.VERIFY_INSTRUCTION),
        HumanMessage(f"待校验的回答：\n{state.get('draft', '')}"),
    ]
    resp = llm.invoke(messages)
    text = str(resp.content).strip().upper()
    return {"verify_pass": text.startswith("PASS"), "messages": [resp]}


def _reflect(state: AgentState) -> dict:
    llm = _get_llm()
    messages = [
        SystemMessage(prompts.SYSTEM_PROMPT + "\n" + prompts.REFLECT_INSTRUCTION),
        HumanMessage("请反思上一轮未通过校验的原因。"),
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

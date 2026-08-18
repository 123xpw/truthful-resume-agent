"""Truthful Resume Agent — 基于 LangChain + LangGraph 的对话 Agent 层。

在现有事实库 / RAG / 溯源校验之上，新增：
- 工具调用（search_facts / verify_fact）
- 检索→生成→校验→反思 闭环
- 短期记忆（对话历史，LangGraph checkpoint）+ 长期记忆（跨会话偏好）
"""

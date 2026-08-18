"""多轮对话入口（REPL）。"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from . import memory
from .graph import build_agent


def main() -> int:
    agent = build_agent()
    config = {"configurable": {"thread_id": "default"}}
    print("Truthful Resume Agent 对话模式（exit 退出；prefs 查看长期记忆；记住 键=值 保存偏好）")
    while True:
        try:
            text = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() == "exit":
            break
        if text.lower() == "prefs":
            prefs = memory.list_preferences()
            print(f"\n长期记忆: {prefs if prefs else '（空）'}")
            continue
        if text.lower().startswith("记住 "):
            content = text[3:].strip()
            if "=" in content:
                key, value = content.split("=", 1)
            elif "：" in content:
                key, value = content.split("：", 1)
            else:
                key, value = "note", content
            memory.save_preference(key.strip(), value.strip())
            print(f"\n已记住：{key.strip()} = {value.strip()}")
            continue
        result = agent.invoke({"messages": [HumanMessage(text)]}, config=config)
        draft = result.get("draft")
        verify_pass = result.get("verify_pass")
        print(f"\nAgent: {draft if draft else '（未生成回答）'}")
        print(f"[校验: {'通过' if verify_pass else '未通过'}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

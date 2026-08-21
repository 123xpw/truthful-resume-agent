"""长期记忆：跨会话的用户偏好，存 JSON 文件（可升级为 Qdrant 向量记忆）。"""

from __future__ import annotations

import json
from pathlib import Path

MEMORY_PATH = Path(__file__).resolve().parents[3] / "data" / "agent_memory.json"


def _load() -> dict:
    if not MEMORY_PATH.exists():
        return {}
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(data: dict) -> None:
    MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_preference(key: str, value: str) -> None:
    data = _load()
    data[key] = value
    _save(data)


def recall_preference(key: str) -> str | None:
    return _load().get(key)


def delete_preference(key: str) -> bool:
    """删除指定偏好，返回是否确实删除了一条。"""
    data = _load()
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def list_preferences() -> dict:
    return _load()

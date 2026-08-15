"""Minimal OpenAI-compatible chat-completion client.

Deliberately thin: one function, no SDK, no retry/streaming machinery —
this project's LLM usage is a handful of short calls per `explain-jd` run,
not a production service.

Configuration is read from the environment, falling back to a local `.env`
file that is gitignored and never committed. The
key is never logged, printed, or embedded in generated output.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


class LLMNotConfigured(RuntimeError):
    pass


def _load_env_file() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def get_api_key() -> str:
    _load_env_file()
    key = os.environ.get("RESUME_AGENT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise LLMNotConfigured(
            "RESUME_AGENT_LLM_API_KEY or DEEPSEEK_API_KEY not set (checked environment and .env)."
        )
    return key


def get_api_url() -> str:
    _load_env_file()
    return os.environ.get("RESUME_AGENT_LLM_API_URL", DEFAULT_API_URL)


def get_model() -> str:
    _load_env_file()
    return os.environ.get("RESUME_AGENT_LLM_MODEL", DEFAULT_MODEL)


def chat_completion(messages: list[dict], temperature: float = 0.2, timeout: float = 60.0) -> str:
    key = get_api_key()
    response = requests.post(
        get_api_url(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": get_model(), "messages": messages, "temperature": temperature},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]

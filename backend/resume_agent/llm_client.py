"""OpenAI-compatible LLM configuration and bounded retry policy.

Both the direct HTTP client and the LangGraph Agent use the same timeout and
retry settings. Retries are limited to transient failures; authentication and
invalid-request failures return immediately.

Configuration is read from the environment, falling back to a local `.env`
file that is gitignored and never committed. The
key is never logged, printed, or embedded in generated output.
"""

from __future__ import annotations

import os
from pathlib import Path
import random
import time
from typing import Callable, TypeVar

import requests

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2

T = TypeVar("T")


class LLMNotConfigured(RuntimeError):
    pass


class LLMServiceError(RuntimeError):
    """A sanitized provider failure safe to expose through an API."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


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


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    _load_env_file()
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    _load_env_file()
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def get_timeout_seconds() -> float:
    return _bounded_float(
        "RESUME_AGENT_LLM_TIMEOUT_SECONDS",
        DEFAULT_TIMEOUT_SECONDS,
        minimum=1.0,
        maximum=120.0,
    )


def get_max_retries() -> int:
    return _bounded_int(
        "RESUME_AGENT_LLM_MAX_RETRIES",
        DEFAULT_MAX_RETRIES,
        minimum=0,
        maximum=5,
    )


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def classify_llm_error(exc: Exception) -> LLMServiceError:
    """Map provider-specific exceptions without leaking response bodies."""
    if isinstance(exc, LLMServiceError):
        return exc
    name = type(exc).__name__
    status = _status_code(exc)
    if isinstance(exc, (requests.Timeout, TimeoutError)) or "Timeout" in name:
        return LLMServiceError("LLM_TIMEOUT", "LLM request timed out.", retryable=True)
    if status == 429 or "RateLimit" in name:
        return LLMServiceError("LLM_RATE_LIMIT", "LLM rate limit exceeded.", retryable=True)
    if isinstance(exc, requests.ConnectionError) or "Connection" in name:
        return LLMServiceError("LLM_UNAVAILABLE", "LLM provider is unavailable.", retryable=True)
    if status in {401, 403} or "Authentication" in name or "PermissionDenied" in name:
        return LLMServiceError("LLM_AUTH_ERROR", "LLM credentials were rejected.", retryable=False)
    if status is not None and 500 <= status <= 599:
        return LLMServiceError("LLM_UNAVAILABLE", "LLM provider returned a server error.", retryable=True)
    if status is not None and 400 <= status <= 499:
        return LLMServiceError("LLM_REQUEST_INVALID", "LLM request was rejected.", retryable=False)
    return LLMServiceError("LLM_CALL_FAILED", "LLM call failed.", retryable=False)


def call_with_retry(
    operation: Callable[[], T],
    *,
    max_retries: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run one provider operation with bounded exponential backoff."""
    retries = get_max_retries() if max_retries is None else max(0, max_retries)
    for attempt in range(retries + 1):
        try:
            return operation()
        except LLMNotConfigured:
            raise
        except Exception as exc:
            mapped = classify_llm_error(exc)
            if not mapped.retryable or attempt >= retries:
                raise mapped from exc
            delay = min(0.25 * (2**attempt) + random.uniform(0.0, 0.1), 2.0)
            sleep(delay)
    raise AssertionError("retry loop exited unexpectedly")


def chat_completion(
    messages: list[dict],
    temperature: float = 0.2,
    timeout: float | None = None,
) -> str:
    key = get_api_key()
    request_timeout = get_timeout_seconds() if timeout is None else timeout

    def request() -> str:
        response = requests.post(
            get_api_url(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": get_model(), "messages": messages, "temperature": temperature},
            timeout=request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMServiceError(
                "LLM_INVALID_RESPONSE",
                "LLM provider returned an invalid response.",
                retryable=False,
            ) from exc
        if not isinstance(content, str):
            raise LLMServiceError(
                "LLM_INVALID_RESPONSE",
                "LLM provider returned non-text content.",
                retryable=False,
            )
        return content

    return call_with_retry(request)

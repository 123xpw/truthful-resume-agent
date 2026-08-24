FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANGGRAPH_STRICT_MSGPACK=true \
    RESUME_AGENT_RUNTIME_DB=/app/data/runtime/agent_runtime.sqlite3

WORKDIR /app

RUN groupadd --system resume-agent \
    && useradd --system --gid resume-agent --home-dir /app resume-agent

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=resume-agent:resume-agent backend ./backend
COPY --chown=resume-agent:resume-agent data ./data
COPY --chown=resume-agent:resume-agent docs ./docs
COPY --chown=resume-agent:resume-agent README.md README.zh-CN.md LICENSE CHANGELOG.md ./

RUN mkdir -p /app/data/runtime /app/data/semantic_index /app/data/jd_library \
    && chown -R resume-agent:resume-agent /app/data

USER resume-agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

CMD ["uvicorn", "backend.resume_agent.web.app:app", "--host", "0.0.0.0", "--port", "8000"]

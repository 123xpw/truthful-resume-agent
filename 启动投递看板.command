#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
RUNTIME_DIR="$PROJECT_DIR/data/runtime"
PID_FILE="$RUNTIME_DIR/outcome_dashboard.pid"
LOG_FILE="$RUNTIME_DIR/outcome_dashboard.log"
UVICORN="$PROJECT_DIR/.venv/bin/uvicorn"
URL="http://127.0.0.1:8000/#outcomes"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$RUNTIME_DIR"

if [[ ! -x "$UVICORN" ]]; then
  echo "未找到项目虚拟环境，请先完成 README 中的本地安装。"
  read "?按回车键关闭..."
  exit 1
fi

if curl --silent --fail "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
  open "$URL"
  exit 0
fi

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 8000 已被其他程序占用，投递看板未启动。"
  read "?按回车键关闭..."
  exit 1
fi

nohup "$UVICORN" backend.resume_agent.web.app:app \
  --host 127.0.0.1 --port 8000 >"$LOG_FILE" 2>&1 </dev/null &
SERVER_PID=$!
echo "$SERVER_PID" >"$PID_FILE"

for _ in {1..40}; do
  if curl --silent --fail "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
    open "$URL"
    exit 0
  fi
  sleep 0.25
done

echo "投递看板启动失败，日志位置：$LOG_FILE"
if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
  kill "$SERVER_PID"
fi
rm -f "$PID_FILE"
read "?按回车键关闭..."
exit 1

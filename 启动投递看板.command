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

if [[ -f "$PID_FILE" ]]; then
  MANAGED_PID="$(tr -cd '0-9' <"$PID_FILE")"
  MANAGED_COMMAND=""
  if [[ -n "$MANAGED_PID" ]]; then
    MANAGED_COMMAND="$(ps -p "$MANAGED_PID" -o command= 2>/dev/null || true)"
  fi
  if [[ "$MANAGED_COMMAND" == *"backend.resume_agent.web.app:app"* ]]; then
    echo "正在重新启动投递看板以加载当前代码..."
    kill "$MANAGED_PID"
    for _ in {1..40}; do
      if ! kill -0 "$MANAGED_PID" >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$MANAGED_PID" >/dev/null 2>&1; then
      echo "旧投递看板未能安全停止，请先运行“停止投递看板.command”。"
      read "?按回车键关闭..."
      exit 1
    fi
  fi
  rm -f "$PID_FILE"
fi

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 8000 已被未受此启动器管理的程序占用，未复用未知或旧版服务。"
  read "?按回车键关闭..."
  exit 1
fi

UVICORN_ARGS=(backend.resume_agent.web.app:app --host 127.0.0.1 --port 8000 --reload --reload-dir "$PROJECT_DIR/backend")
nohup "$UVICORN" "${UVICORN_ARGS[@]}" >"$LOG_FILE" 2>&1 </dev/null &
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

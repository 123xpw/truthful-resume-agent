#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
PID_FILE="$PROJECT_DIR/data/runtime/outcome_dashboard.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "没有发现由启动器管理的投递看板进程。"
  read "?按回车键关闭..."
  exit 0
fi

SERVER_PID="$(tr -cd '0-9' <"$PID_FILE")"
if [[ -z "$SERVER_PID" ]]; then
  echo "进程记录无效，未停止任何程序。"
  rm -f "$PID_FILE"
  read "?按回车键关闭..."
  exit 1
fi

COMMAND_LINE="$(ps -p "$SERVER_PID" -o command= 2>/dev/null || true)"
if [[ "$COMMAND_LINE" != *"backend.resume_agent.web.app:app"* ]]; then
  echo "进程身份不匹配，出于安全原因未执行停止操作。"
  rm -f "$PID_FILE"
  read "?按回车键关闭..."
  exit 1
fi

kill "$SERVER_PID"
rm -f "$PID_FILE"
echo "投递看板已停止。"

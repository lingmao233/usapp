#!/usr/bin/env bash
# 同时启动后端 uvicorn(8000) 和前端 vite(默认 7100)。
# CLI 传入的 host/port 参数（如 npm run dev -- --port 7200）会转发给 vite。
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT/server"
# Windows venv 用 Scripts/python.exe，macOS/Linux 用 bin/python；
# 若 .venv 是从 Windows 拷来的（bin/ 下没有 python），回退到 .venv-mac
if [ "$OS" = "Windows_NT" ] && [ -x "$SERVER_DIR/.venv/Scripts/python.exe" ]; then
  VENV_PY="$SERVER_DIR/.venv/Scripts/python.exe"
elif [ -x "$SERVER_DIR/.venv/bin/python" ]; then
  VENV_PY="$SERVER_DIR/.venv/bin/python"
elif [ -x "$SERVER_DIR/.venv-mac/bin/python" ]; then
  VENV_PY="$SERVER_DIR/.venv-mac/bin/python"
else
  echo "找不到可用的 Python 虚拟环境：请在 server/ 下 python3 -m venv .venv 并安装 requirements.txt" >&2
  exit 1
fi

UVICORN_PID=""
VITE_PID=""

cleanup() {
  [ -n "$UVICORN_PID" ] && kill "$UVICORN_PID" 2>/dev/null || true
  [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Windows 控制台默认 GBK，后端中文日志会乱码；统一切成 UTF-8
if [ "$OS" = "Windows_NT" ]; then
  chcp.com 65001 >/dev/null 2>&1 || true
  export PYTHONIOENCODING=utf-8
fi

cd "$SERVER_DIR"
# 开发模式必须带 --reload：否则后端代码变更不会生效，
# 旧进程会和新前端产生接口不一致（这正是"身份码生成卡住"类问题的根因）
"$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
UVICORN_PID=$!

cd "$ROOT"
npx vite "$@" &
VITE_PID=$!

wait

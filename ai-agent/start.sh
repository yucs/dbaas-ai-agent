#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/backend"
APP_MODULE="dbass_ai_agent.main:app"
APP_DIR="${BACKEND_DIR}/src"
DATA_ROOT="${DBASS_AGENT_DATA_ROOT:-${SCRIPT_DIR}/data/users}"
HOST="${DBASS_AGENT_HOST:-127.0.0.1}"
PORT="${DBASS_AGENT_PORT:-8010}"

if [[ -x "${SCRIPT_DIR}/../mock-server/.venv/bin/python" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/../mock-server/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "未找到可用的 Python 解释器，请先准备 Python 3.11+ 环境。" >&2
  exit 1
fi

mkdir -p "${DATA_ROOT}"

echo "Starting dbass-ai-agent"
echo "  python : ${PYTHON_BIN}"
echo "  host   : ${HOST}"
echo "  port   : ${PORT}"
echo "  data   : ${DATA_ROOT}"

export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export DBASS_AGENT_DATA_ROOT="${DATA_ROOT}"

exec "${PYTHON_BIN}" -m uvicorn "${APP_MODULE}" --app-dir "${APP_DIR}" --host "${HOST}" --port "${PORT}" --reload

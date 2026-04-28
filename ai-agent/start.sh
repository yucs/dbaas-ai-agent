#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/backend"
MOCK_SERVER_DIR="${SCRIPT_DIR}/../mock-server"
MOCK_SERVER_START="${MOCK_SERVER_DIR}/start.sh"
APP_MODULE="dbass_ai_agent.main:app"
APP_DIR="${BACKEND_DIR}/src"
CONFIG_FILE="${SCRIPT_DIR}/config.toml"
MOCK_SERVER_PID=""
BACKEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    echo "Stopping dbass-ai-agent pid=${BACKEND_PID}"
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
    wait "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${MOCK_SERVER_PID}" ]] && kill -0 "${MOCK_SERVER_PID}" >/dev/null 2>&1; then
    echo "Stopping dbaas-mock-server pid=${MOCK_SERVER_PID}"
    kill "${MOCK_SERVER_PID}" >/dev/null 2>&1 || true
    wait "${MOCK_SERVER_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

if [[ -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${BACKEND_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "未找到可用的 Python 解释器，请先准备 Python 3.11+ 环境。" >&2
  exit 1
fi

export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "缺少配置文件: ${CONFIG_FILE}" >&2
  echo "请先从 ai-agent/config.example.toml 复制一份为 ai-agent/config.toml 并填写模型配置。" >&2
  exit 1
fi

IFS=$'\t' read -r HOST PORT DATA_ROOT RUNTIME_ROOT < <("${PYTHON_BIN}" - <<'PY'
from dbass_ai_agent.config import ConfigError, get_settings

try:
    settings = get_settings()
except ConfigError as exc:
    print(str(exc))
    raise SystemExit(1)

print("\t".join([
    settings.host,
    str(settings.port),
    str(settings.data_root),
    str(settings.runtime_root),
]))
PY
) || {
  echo "读取配置失败，请检查 config.toml。" >&2
  exit 1
}

mkdir -p "${DATA_ROOT}" "${RUNTIME_ROOT}"

if [[ ! -x "${MOCK_SERVER_START}" ]]; then
  echo "缺少或不可执行的 dbaas-mock-server 启动脚本: ${MOCK_SERVER_START}" >&2
  exit 1
fi

echo "Starting dbaas-mock-server"
echo "  script : ${MOCK_SERVER_START}"
echo "  host   : 127.0.0.1"
echo "  port   : 9000"
(
  cd "${MOCK_SERVER_DIR}"
  HOST="127.0.0.1" PORT="9000" ./start.sh
) &
MOCK_SERVER_PID="$!"

echo "Starting dbass-ai-agent"
echo "  python : ${PYTHON_BIN}"
echo "  config : ${CONFIG_FILE}"
echo "  host   : ${HOST}"
echo "  port   : ${PORT}"
echo "  data   : ${DATA_ROOT}"

"${PYTHON_BIN}" -m uvicorn \
  "${APP_MODULE}" \
  --app-dir "${APP_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --reload \
  --reload-dir "${SCRIPT_DIR}" \
  --reload-dir "${APP_DIR}" &
BACKEND_PID="$!"

wait "${BACKEND_PID}"

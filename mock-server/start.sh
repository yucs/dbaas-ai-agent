#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

OS_NAME="$(uname -s)"
case "$OS_NAME" in
  Darwin|Linux)
    ;;
  *)
    echo "Unsupported OS: $OS_NAME"
    echo "This script currently supports macOS and Linux."
    exit 1
    ;;
esac

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-false}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN"
  echo "Please install Python 3.11+ or override PYTHON_BIN."
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating virtual environment at $VENV_DIR ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c "import fastapi, uvicorn, pydantic" >/dev/null 2>&1; then
  echo "Installing dependencies from requirements.txt ..."
  "$VENV_DIR/bin/pip" install -r requirements.txt
fi

echo "Starting DBaaS Mock Server on http://${HOST}:${PORT} ..."

UVICORN_ARGS=(app.main:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
fi

exec "$VENV_DIR/bin/python" -m uvicorn "${UVICORN_ARGS[@]}"

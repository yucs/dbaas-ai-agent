#!/usr/bin/env bash

set -euo pipefail

kill_port() {
  local port="$1"
  local pids

  if ! command -v lsof >/dev/null 2>&1; then
    echo "lsof is required to stop processes by port." >&2
    exit 1
  fi

  pids="$(lsof -ti "tcp:${port}" || true)"
  if [[ -z "${pids}" ]]; then
    echo "No process listening on port ${port}"
    return
  fi

  echo "Stopping port ${port}: ${pids}"
  kill ${pids} >/dev/null 2>&1 || true
}

kill_port 8010
kill_port 9000

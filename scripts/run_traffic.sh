#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VENV_PATH="${VENV_PATH:-}"
if [[ -z "$VENV_PATH" ]]; then
  if [[ -f "$ROOT/.mnvenv/bin/activate" ]]; then
    VENV_PATH="$ROOT/.mnvenv/bin/activate"
  elif [[ -f "$ROOT/.venv/bin/activate" ]]; then
    VENV_PATH="$ROOT/.venv/bin/activate"
  fi
fi

if [[ -z "$VENV_PATH" ]]; then
  echo "Virtualenv not found. Set VENV_PATH or create .mnvenv/.venv." >&2
  exit 2
fi

# shellcheck disable=SC1090
source "$VENV_PATH"

usage() {
  cat <<'EOF'
Usage: scripts/run_traffic.sh [--pid <mininet_pid>]

This script tries to inject a traffic command into a running Mininet CLI.
Set TRAFFIC_* env vars before running to override defaults.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  exec sudo -E "$0" "$@"
fi


PID=""
if [[ "${1:-}" == "--pid" ]]; then
  PID="${2:-}"
  shift 2 || true
fi

if [[ -z "$PID" ]]; then
  mapfile -t PIDS < <(pgrep -f "topologies/.*\.py" || true)
  if (( ${#PIDS[@]} == 1 )); then
    PID="${PIDS[0]}"
  elif (( ${#PIDS[@]} > 1 )); then
    echo "Multiple Mininet topology processes found." >&2
    ps -o pid,cmd -p "${PIDS[@]}" >&2
    echo "Re-run with --pid <pid>." >&2
    exit 2
  fi
fi

if [[ -z "$PID" ]]; then
  echo "No Mininet topology process found." >&2
  echo "Run this from the Mininet CLI instead:" >&2
  echo "  py import sys; sys.path.insert(0, \"$ROOT\"); __import__(\"traffic.generate_traffic\", fromlist=[\"run_from_env\"]).run_from_env(net)" >&2
  exit 2
fi

if [[ ! -w "/proc/$PID/fd/0" ]]; then
  echo "Mininet CLI stdin is not writable for pid $PID." >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

PY_LINE="$(PROJECT_ROOT="$ROOT" "$PYTHON_BIN" - <<'PY'
import os
root = os.environ["PROJECT_ROOT"]
keys = [
    "TRAFFIC_PAIRS",
    "TRAFFIC_PROTOCOLS",
    "TRAFFIC_PAIR_COUNT",
    "TRAFFIC_PAIR_MODE",
    "TRAFFIC_FLOWS_PER_PAIR",
    "TRAFFIC_EPISODES",
    "TRAFFIC_DURATION",
    "TRAFFIC_INTERVAL",
    "TRAFFIC_LINK_BW_Mbps",
    "TRAFFIC_STAGGER_MIN",
    "TRAFFIC_STAGGER_MAX",
    "TRAFFIC_OUTPUT",
    "TRAFFIC_PING",
    "TRAFFIC_VERBOSE",
    "TRAFFIC_SEED",
    "TRAFFIC_FEEDBACK_HOST",
    "TRAFFIC_FEEDBACK_PORT",
    "TRAFFIC_FEEDBACK_GRACE",
    "TRAFFIC_CONCURRENT",
]
lines = ["import os, sys", f"sys.path.insert(0, {root!r})"]
for key in keys:
    val = os.environ.get(key)
    if val is None:
        continue
    lines.append(f"os.environ[{key!r}] = {val!r}")
lines.append("__import__('traffic.generate_traffic', fromlist=['run_from_env']).run_from_env(net)")
print(f"exec({chr(10).join(lines)!r})")
PY
)"

printf 'py %s\n' "$PY_LINE" > "/proc/$PID/fd/0"

echo "Sent traffic command to Mininet CLI (pid=$PID)."

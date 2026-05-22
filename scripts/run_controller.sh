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

if [[ "${SKIP_MININET_CLEANUP:-0}" != "1" ]] && command -v mn >/dev/null 2>&1; then
  echo "[INFO] Cleaning Mininet..."
  sudo mn -c
fi

usage() {
  cat <<'EOF'
Usage: scripts/run_controller.sh [controller] [topology]

controller:
  dijkstra | rlearner | roptimizer | staged_controller | path/to/controller.py

topology:
  abilene | fat_tree | abilene_imp | <topology_data module name>
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

CONTROLLER="${1:-dijkstra}"
TOPOLOGY="${2:-abilene}"

case "$CONTROLLER" in
  dijkstra|rlearner|roptimizer|staged_controller)
    CONTROLLER_PATH="$ROOT/controllers/${CONTROLLER}.py"
    ;;
  *.py)
    if [[ "$CONTROLLER" = /* ]]; then
      CONTROLLER_PATH="$CONTROLLER"
    else
      CONTROLLER_PATH="$ROOT/$CONTROLLER"
    fi
    ;;
  *)
    echo "Unknown controller: $CONTROLLER" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ ! -f "$CONTROLLER_PATH" ]]; then
  echo "Controller not found: $CONTROLLER_PATH" >&2
  exit 2
fi

RYU_MANAGER="${RYU_MANAGER:-ryu-manager}"
export TOPOLOGY="$TOPOLOGY"

exec "$RYU_MANAGER" "$CONTROLLER_PATH"

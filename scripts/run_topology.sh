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

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "python not found in the virtualenv." >&2
  exit 2
fi



usage() {
  cat <<'EOF'
Usage: scripts/run_topology.sh [topology]

topology:
  abilene | fat_tree | abilene_imp | path/to/topology.py
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TOPOLOGY="${1:-abilene}"

case "$TOPOLOGY" in
  abilene|fat_tree|abilene_imp)
    TOPOLOGY_PATH="$ROOT/topologies/${TOPOLOGY}.py"
    ;;
  *.py)
    if [[ "$TOPOLOGY" = /* ]]; then
      TOPOLOGY_PATH="$TOPOLOGY"
    else
      TOPOLOGY_PATH="$ROOT/$TOPOLOGY"
    fi
    ;;
  *)
    echo "Unknown topology: $TOPOLOGY" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ ! -f "$TOPOLOGY_PATH" ]]; then
  echo "Topology not found: $TOPOLOGY_PATH" >&2
  exit 2
fi

: "${AUTO_TRAFFIC:=0}"
: "${TRAFFIC_PROTOCOLS:=both}"
: "${TRAFFIC_PAIR_COUNT:=3}"
: "${TRAFFIC_PAIR_MODE:=ends}"
: "${TRAFFIC_FLOWS_PER_PAIR:=1}"
: "${TRAFFIC_EPISODES:=1}"
: "${TRAFFIC_DURATION:=60}"
: "${TRAFFIC_INTERVAL:=1}"
: "${TRAFFIC_LINK_BW_Mbps:=100}"
: "${TRAFFIC_STAGGER_MIN:=1}"
: "${TRAFFIC_STAGGER_MAX:=3}"
: "${TRAFFIC_OUTPUT:=results/traffic.csv}"
: "${TRAFFIC_PING:=0}"
: "${TRAFFIC_VERBOSE:=1}"

export AUTO_TRAFFIC
export TRAFFIC_PROTOCOLS
export TRAFFIC_PAIR_COUNT
export TRAFFIC_PAIR_MODE
export TRAFFIC_FLOWS_PER_PAIR
export TRAFFIC_EPISODES
export TRAFFIC_DURATION
export TRAFFIC_INTERVAL
export TRAFFIC_LINK_BW_Mbps
export TRAFFIC_STAGGER_MIN
export TRAFFIC_STAGGER_MAX
export TRAFFIC_OUTPUT
export TRAFFIC_PING
export TRAFFIC_VERBOSE

exec sudo -E "$PYTHON_BIN" "$TOPOLOGY_PATH"

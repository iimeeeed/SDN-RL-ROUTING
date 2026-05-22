#!/usr/bin/env bash
set -euo pipefail

echo "[INFO] Cleaning Mininet..."
sudo mn -c

echo "[INFO] Killing old Ryu processes..."
pkill -f ryu-manager || true

echo "[INFO] Killing old iPerf processes..."
pkill -f iperf || true
pkill -f iperf3 || true

echo "[INFO] Done."

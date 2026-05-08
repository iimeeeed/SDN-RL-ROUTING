#!/usr/bin/env bash

echo "[INFO] Cleaning Mininet..."
sudo mn -c

echo "[INFO] Killing old Ryu processes..."
pkill -f ryu-manager || true

echo "[INFO] Killing old iperf3 processes..."
pkill -f iperf3 || true

echo "[INFO] Done."

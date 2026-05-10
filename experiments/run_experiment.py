#!/usr/bin/env python3
"""
Master experiment runner (Member 4).

Loads experiments/config.yaml (or a path you pass), starts the chosen Ryu
controller, brings up Mininet via topologies.create_network(), runs iPerf3 +
ping for each episode, and appends rows to a CSV under results/.

Typical usage (Linux VM with Mininet; requires root for Mininet):

    sudo $(which python3) experiments/run_experiment.py --config experiments/config.yaml

Dry-run (print resolved settings only):

    python3 experiments/run_experiment.py --config experiments/config.yaml --dry-run
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent

CONTROLLER_SCRIPTS = {
    "dijkstra": REPO_ROOT / "controllers" / "dijkstra.py",
    "ecmp": REPO_ROOT / "controllers" / "ecmp.py",
}

CSV_COLUMNS = [
    "timestamp_utc",
    "episode",
    "condition",
    "topology",
    "controller",
    "throughput_gbps",
    "jitter_ms",
    "rtt_ms",
    "plr_pct",
    "cumulative_reward",
    "flow_reconfigs",
    "sanity_flag",
    "notes",
]


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_topology_module(topology: str):
    sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(f"topologies.{topology}")


def parse_ping_rtt_ms(ping_out: str) -> float | None:
    """Parse average RTT from Linux ``ping -c N`` summary line."""
    m = re.search(
        r"min/avg/max(?:/mdev)?\s*=\s*[\d.]+\/([\d.]+)\/",
        ping_out,
        re.MULTILINE,
    )
    if not m:
        return None
    return float(m.group(1))


def run_ping_rtt(net, client_name: str, server_ip: str, count: int = 10) -> float | None:
    h = net.get(client_name)
    out = h.cmd(f"ping -c {count} -W 1 {server_ip}")
    return parse_ping_rtt_ms(out)


def run_iperf_tcp_gbps(
    net, server_name: str, client_name: str, duration: int
) -> float | None:
    srv = net.get(server_name)
    cli = net.get(client_name)
    sip = srv.IP()
    srv.cmd("killall -q iperf3 2>/dev/null || true")
    srv.cmd("iperf3 -s -D 2>/dev/null || true")
    time.sleep(0.5)
    out = cli.cmd(f"iperf3 -c {sip} -t {duration} -f g --json 2>/dev/null")
    srv.cmd("killall -q iperf3 2>/dev/null || true")
    try:
        data = json.loads(out)
        bps = data["end"]["sum_received"].get("bits_per_second")
        if bps is None:
            return None
        return float(bps) / 1e9
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def run_iperf_udp_metrics(
    net,
    server_name: str,
    client_name: str,
    duration: int,
    udp_bitrate: str,
) -> tuple[float | None, float | None]:
    srv = net.get(server_name)
    cli = net.get(client_name)
    srv.cmd("killall -q iperf3 2>/dev/null || true")
    srv.cmd("iperf3 -s -D 2>/dev/null || true")
    time.sleep(0.5)
    sip = srv.IP()
    out = cli.cmd(
        f"iperf3 -c {sip} -u -b {udp_bitrate} -t {duration} "
        f"--json 2>/dev/null"
    )
    srv.cmd("killall -q iperf3 2>/dev/null || true")
    jitter_ms = None
    plr = None
    try:
        data = json.loads(out)
        stream = data["end"]["streams"][0]["udp"]
        jitter_ms = float(stream.get("jitter_ms", 0))
        lost = stream.get("lost_packets", 0)
        total = stream.get("packets", 0) or 1
        plr = 100.0 * float(lost) / float(total)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    return jitter_ms, plr


def ensure_results_path(results_dir: Path, condition: str, topology: str) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = f"{condition}_{topology}_{stamp}.csv".replace(" ", "_")
    return results_dir / safe


def write_csv_header(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()


def append_csv_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writerow(row)


def sanity_check(
    history: list[float],
    value: float | None,
    sigma: float = 3.0,
) -> str:
    """
    Flag outliers vs running mean of prior episodes (project plan: >3σ).
    """
    if value is None or math.isnan(value):
        return "missing_metric"
    if len(history) < 2:
        return "ok"
    mu = sum(history) / len(history)
    var = sum((x - mu) ** 2 for x in history) / len(history)
    std = math.sqrt(var)
    if std < 1e-12:
        return "ok"
    if abs(value - mu) > sigma * std:
        return "outlier"
    return "ok"


def start_ryu(controller_key: str, topology: str, listen_wait: float) -> subprocess.Popen:
    script = CONTROLLER_SCRIPTS.get(controller_key)
    if script is None:
        raise SystemExit(f"Unknown controller key: {controller_key!r}")
    if not script.is_file():
        raise SystemExit(f"Controller script missing: {script}")

    ryu_bin = shutil.which("ryu-manager")
    if not ryu_bin:
        raise SystemExit("ryu-manager not found in PATH (activate your venv).")

    env = os.environ.copy()
    env["TOPOLOGY"] = topology

    proc = subprocess.Popen(
        [ryu_bin, str(script)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(listen_wait)
    if proc.poll() is not None:
        raise SystemExit("ryu-manager exited early; check TOPOLOGY and controller logs.")
    return proc


def run_episodes(cfg: dict[str, Any], csv_path: Path, dry_run: bool) -> None:
    exp = cfg["experiment"]
    traf = cfg["traffic"]
    ryu_cfg = cfg.get("ryu") or {}
    udp_rate = cfg.get("iperf_udp_bitrate", "50M")

    condition = str(exp["condition"])
    topology = str(exp["topology"])
    controller = str(exp["controller"])
    n_episodes = int(exp.get("episodes", 1))
    duration = int(exp.get("episode_duration_sec", 60))
    results_sub = Path(exp.get("results_dir", "results"))

    server = str(traf["server_host"])
    client = str(traf["client_host"])
    listen_wait = float(ryu_cfg.get("listen_wait_sec", 4))

    results_dir = REPO_ROOT / results_sub

    if dry_run:
        print("Dry run — resolved configuration:")
        print(f"  condition={condition} topology={topology} controller={controller}")
        print(f"  episodes={n_episodes} duration_sec={duration}")
        print(f"  traffic {client} -> {server}")
        print(f"  results_dir={results_dir}")
        print(f"  CSV would be: {csv_path}")
        return

    if os.geteuid() != 0:
        print(
            "Warning: Mininet normally requires root. Re-run with sudo if net.start fails.",
            file=sys.stderr,
        )

    write_csv_header(csv_path)

    ryu_proc = start_ryu(controller, topology, listen_wait)
    tp_hist: list[float] = []

    try:
        topo_mod = resolve_topology_module(topology)
        net = topo_mod.create_network()
        try:
            srv = net.get(server)
            cli = net.get(client)
            server_ip = srv.IP()

            for ep in range(1, n_episodes + 1):
                notes: list[str] = []

                _ = run_ping_rtt(net, client, server_ip, count=3)

                tp = run_iperf_tcp_gbps(net, server, client, duration)
                jit, plr = run_iperf_udp_metrics(
                    net, server, client, duration, udp_rate
                )
                rtt = run_ping_rtt(net, client, server_ip, count=min(20, duration))

                flag = sanity_check(tp_hist, tp)
                if tp is not None:
                    tp_hist.append(tp)
                if flag == "outlier":
                    notes.append("throughput_outlier_vs_prior_episodes")

                row = {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "episode": ep,
                    "condition": condition,
                    "topology": topology,
                    "controller": controller,
                    "throughput_gbps": "" if tp is None else f"{tp:.6f}",
                    "jitter_ms": "" if jit is None else f"{jit:.6f}",
                    "rtt_ms": "" if rtt is None else f"{rtt:.6f}",
                    "plr_pct": "" if plr is None else f"{plr:.6f}",
                    "cumulative_reward": "",
                    "flow_reconfigs": "",
                    "sanity_flag": flag,
                    "notes": ";".join(notes),
                }
                append_csv_row(csv_path, row)
                print(f"Episode {ep}/{n_episodes} wrote row to {csv_path.name}")

        finally:
            net.stop()

    finally:
        ryu_proc.send_signal(signal.SIGTERM)
        try:
            ryu_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ryu_proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser(description="SDN-RL routing experiment runner")
    ap.add_argument(
        "--config",
        default=str(REPO_ROOT / "experiments" / "config.yaml"),
        help="Path to YAML config",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved settings and exit (no Mininet / Ryu)",
    )
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    if not cfg_path.is_file():
        raise SystemExit(f"Config not found: {cfg_path}")

    cfg = load_config(cfg_path)
    exp = cfg["experiment"]
    results_sub = Path(exp.get("results_dir", "results"))
    csv_path = ensure_results_path(
        REPO_ROOT / results_sub,
        str(exp["condition"]),
        str(exp["topology"]),
    )

    run_episodes(cfg, csv_path, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"Done. CSV: {csv_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

"""
Run controller/topology traffic experiments end to end.

The runner starts one Ryu controller, starts one Mininet topology, injects the
traffic command through scripts/run_traffic.sh, stores per-run CSV/log files,
and writes a compact summary CSV.
"""

import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "results" / "experiments"

DEFAULT_CONTROLLERS = ["dijkstra", "rlearner", "roptimizer", "staged_controller"]
DEFAULT_TOPOLOGIES = ["abilene"]
BASH = os.environ.get("BASH", "bash")


def script_cmd(script_name: str, *args: str) -> List[str]:
    return [BASH, str(ROOT / "scripts" / script_name), *args]


def parse_csv_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def make_env(args: argparse.Namespace, controller: str, topology: str,
             output_path: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["TOPOLOGY"] = topology
    env["SKIP_MININET_CLEANUP"] = "1"
    env["NONINTERACTIVE_SUDO"] = "1"
    env["LEARNED_FLOW_IDLE_TIMEOUT"] = str(args.learned_flow_idle_timeout)

    if controller == "staged_controller":
        env["EXPLORE_DECISIONS"] = str(args.explore_decisions)

    env["TRAFFIC_PAIR_COUNT"] = str(args.pair_count)
    env["TRAFFIC_PAIR_MODE"] = args.pair_mode
    env["TRAFFIC_PROTOCOLS"] = args.protocols
    env["TRAFFIC_DURATION"] = str(args.duration)
    env["TRAFFIC_EPISODES"] = str(args.episodes)
    env["TRAFFIC_FLOWS_PER_PAIR"] = str(args.flows_per_pair)
    env["TRAFFIC_INTERVAL"] = str(args.interval)
    env["TRAFFIC_LINK_BW_Mbps"] = str(args.link_bw_mbps)
    env["TRAFFIC_STAGGER_MIN"] = str(args.stagger_min)
    env["TRAFFIC_STAGGER_MAX"] = str(args.stagger_max)
    env["TRAFFIC_OUTPUT"] = str(output_path)
    env["TRAFFIC_PING"] = "1" if args.ping else "0"
    env["TRAFFIC_VERBOSE"] = "1" if args.verbose_traffic else "0"

    if args.seed is not None:
        env["TRAFFIC_SEED"] = str(args.seed)

    if args.pairs:
        env["TRAFFIC_PAIRS"] = args.pairs

    return env


def open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", buffering=1)


def start_process(name: str, cmd: List[str], env: Dict[str, str], log_path: Path,
                  stdin_pipe: bool = False) -> subprocess.Popen:
    log = open_log(log_path)
    print(f"[experiment] starting {name}: {' '.join(cmd)}")
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def stop_process(name: str, proc: Optional[subprocess.Popen], timeout: float = 8.0) -> None:
    if proc is None or proc.poll() is not None:
        return

    print(f"[experiment] stopping {name} pid={proc.pid}")
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[experiment] killing {name} pid={proc.pid}")
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=timeout)


def run_command(name: str, cmd: List[str], env: Optional[Dict[str, str]] = None,
                timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    print(f"[experiment] running {name}: {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env or os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def require_cached_sudo() -> None:
    result = run_command("sudo preflight", ["sudo", "-n", "true"], timeout=10)
    if result.returncode == 0:
        return

    raise RuntimeError(
        "sudo is required for Mininet, but sudo is not authenticated for "
        "non-interactive use. Run `sudo -v` in this terminal, then rerun "
        "the experiment."
    )


def wait_for_process(proc: subprocess.Popen, seconds: float, name: str) -> None:
    time.sleep(seconds)
    if proc.poll() is not None:
        raise RuntimeError(f"{name} exited early with code {proc.returncode}")


def send_mininet_command(proc: subprocess.Popen, command: str) -> None:
    if proc.stdin is None:
        raise RuntimeError("topology process has no writable stdin")
    proc.stdin.write(command.rstrip() + "\n")
    proc.stdin.flush()


def summarize_csv(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {
            "rows": 0,
            "tcp_rows": 0,
            "udp_rows": 0,
            "avg_tcp_throughput_mbps": "",
            "avg_udp_jitter_ms": "",
            "avg_udp_loss_pct": "",
            "avg_rtt_ms": "",
        }

    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    def floats(field: str, protocol: Optional[str] = None) -> List[float]:
        values = []
        for row in rows:
            if protocol and row.get("protocol") != protocol:
                continue
            raw = row.get(field)
            if raw in (None, ""):
                continue
            try:
                values.append(float(raw))
            except ValueError:
                continue
        return values

    def avg(values: Iterable[float]) -> str:
        values = list(values)
        if not values:
            return ""
        return f"{sum(values) / len(values):.6f}"

    tcp_rows = [row for row in rows if row.get("protocol") == "tcp"]
    udp_rows = [row for row in rows if row.get("protocol") == "udp"]

    return {
        "rows": len(rows),
        "tcp_rows": len(tcp_rows),
        "udp_rows": len(udp_rows),
        "avg_tcp_throughput_mbps": avg(floats("throughput_mbps", "tcp")),
        "avg_udp_jitter_ms": avg(floats("jitter_ms", "udp")),
        "avg_udp_loss_pct": avg(floats("loss_pct", "udp")),
        "avg_rtt_ms": avg(floats("rtt_ms")),
    }


def wait_for_traffic_output(path: Path, timeout: int, stable_seconds: float = 2.0) -> None:
    deadline = time.time() + timeout
    last_size = -1
    stable_since = None

    while time.time() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size > 0 and size == last_size:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_seconds:
                    return
            else:
                stable_since = None
                last_size = size
        time.sleep(0.5)

    raise RuntimeError(f"traffic output missing or incomplete: {path}")


def write_summary(summary_path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return

    fieldnames = [
        "run_id",
        "controller",
        "topology",
        "status",
        "traffic_csv",
        "controller_log",
        "topology_log",
        "traffic_log",
        "rows",
        "tcp_rows",
        "udp_rows",
        "avg_tcp_throughput_mbps",
        "avg_udp_jitter_ms",
        "avg_udp_loss_pct",
        "avg_rtt_ms",
        "error",
    ]
    with summary_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_one(args: argparse.Namespace, experiment_dir: Path, controller: str,
            topology: str, index: int) -> Dict[str, object]:
    run_id = f"{index:02d}_{controller}_{topology}"
    run_dir = experiment_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    traffic_csv = run_dir / "traffic.csv"
    controller_log = run_dir / "controller.log"
    topology_log = run_dir / "topology.log"
    traffic_log = run_dir / "traffic_runner.log"

    env = make_env(args, controller, topology, traffic_csv)

    controller_proc = None
    topology_proc = None
    status = "failed"
    error = ""

    try:
        cleanup = run_command(
            "cleanup",
            script_cmd("clean_mininet.sh"),
            env=env,
            timeout=args.cleanup_timeout,
        )
        (run_dir / "cleanup.log").write_text(cleanup.stdout)

        controller_proc = start_process(
            "controller",
            script_cmd("run_controller.sh", controller, topology),
            env,
            controller_log,
        )
        wait_for_process(controller_proc, args.controller_startup_delay, "controller")

        topology_proc = start_process(
            "topology",
            script_cmd("run_topology.sh", topology),
            env,
            topology_log,
            stdin_pipe=True,
        )
        wait_for_process(topology_proc, args.topology_startup_delay, "topology")

        if args.pingall:
            print("[experiment] running Mininet pingall")
            send_mininet_command(topology_proc, "pingall")
            wait_for_process(topology_proc, args.pingall_wait, "topology")

        traffic = run_command(
            "traffic",
            script_cmd("run_traffic.sh", "--pid", str(topology_proc.pid)),
            env=env,
            timeout=args.traffic_timeout,
        )
        traffic_log.write_text(traffic.stdout)
        if traffic.returncode != 0:
            raise RuntimeError(f"traffic runner failed with code {traffic.returncode}")

        wait_for_traffic_output(traffic_csv, args.traffic_timeout)

        status = "ok"

    except Exception as exc:
        error = str(exc)
        print(f"[experiment] {run_id} failed: {error}")

    finally:
        if topology_proc is not None and topology_proc.poll() is None:
            try:
                send_mininet_command(topology_proc, "exit")
                topology_proc.wait(timeout=8)
            except Exception:
                stop_process("topology", topology_proc)

        stop_process("controller", controller_proc)
        cleanup = run_command(
            "cleanup",
            script_cmd("clean_mininet.sh"),
            env=env,
            timeout=args.cleanup_timeout,
        )
        (run_dir / "cleanup_final.log").write_text(cleanup.stdout)

    summary = summarize_csv(traffic_csv)
    summary.update({
        "run_id": run_id,
        "controller": controller,
        "topology": topology,
        "status": status,
        "traffic_csv": str(traffic_csv),
        "controller_log": str(controller_log),
        "topology_log": str(topology_log),
        "traffic_log": str(traffic_log),
        "error": error,
    })
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run SDN routing controller experiments end to end."
    )
    parser.add_argument(
        "--controllers",
        default=",".join(DEFAULT_CONTROLLERS),
        help="Comma-separated controllers to test.",
    )
    parser.add_argument(
        "--topologies",
        default=",".join(DEFAULT_TOPOLOGIES),
        help="Comma-separated topologies to test.",
    )
    parser.add_argument("--pair-count", type=int, default=2)
    parser.add_argument("--pair-mode", default="ends")
    parser.add_argument("--pairs", default="")
    parser.add_argument("--protocols", default="both")
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--episodes", type=int, default=2)
    parser.add_argument("--flows-per-pair", type=int, default=1)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--link-bw-mbps", type=int, default=100)
    parser.add_argument("--stagger-min", type=float, default=0.2)
    parser.add_argument("--stagger-max", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ping", action="store_true")
    parser.add_argument("--verbose-traffic", action="store_true")
    parser.add_argument("--explore-decisions", type=int, default=50)
    parser.add_argument("--learned-flow-idle-timeout", type=int, default=1)
    parser.add_argument("--controller-startup-delay", type=float, default=4.0)
    parser.add_argument("--topology-startup-delay", type=float, default=6.0)
    parser.add_argument("--pingall", action="store_true")
    parser.add_argument("--pingall-wait", type=float, default=8.0)
    parser.add_argument("--traffic-timeout", type=int, default=300)
    parser.add_argument("--cleanup-timeout", type=int, default=60)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    controllers = parse_csv_list(args.controllers)
    topologies = parse_csv_list(args.topologies)

    if not controllers:
        raise SystemExit("No controllers selected")
    if not topologies:
        raise SystemExit("No topologies selected")

    experiment_dir = Path(args.output_dir) if args.output_dir else RESULTS_ROOT / timestamp()
    if not experiment_dir.is_absolute():
        experiment_dir = ROOT / experiment_dir
    experiment_dir.mkdir(parents=True, exist_ok=True)

    print(f"[experiment] output_dir={experiment_dir}")
    print(f"[experiment] controllers={controllers}")
    print(f"[experiment] topologies={topologies}")

    try:
        require_cached_sudo()
    except RuntimeError as exc:
        print(f"[experiment] {exc}")
        return 1

    rows: List[Dict[str, object]] = []
    run_index = 1

    for topology in topologies:
        for controller in controllers:
            row = run_one(args, experiment_dir, controller, topology, run_index)
            rows.append(row)
            write_summary(experiment_dir / "summary.csv", rows)
            run_index += 1

            if args.fail_fast and row["status"] != "ok":
                print("[experiment] fail-fast enabled, stopping")
                print(f"[experiment] summary={experiment_dir / 'summary.csv'}")
                return 1

    failed = [row for row in rows if row["status"] != "ok"]
    print(f"[experiment] summary={experiment_dir / 'summary.csv'}")
    print(f"[experiment] completed={len(rows) - len(failed)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

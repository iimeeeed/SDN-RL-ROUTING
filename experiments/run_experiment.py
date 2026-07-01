#!/usr/bin/env python3

"""Run controller/topology/traffic experiment matrices.

The runner intentionally delegates network behavior to the repo's existing
entry points:
  - scripts/run_controller.sh
  - scripts/run_topology.sh
  - traffic.generate_traffic.run_from_env(net)

Each matrix item writes controller/topology logs, traffic CSV, completion
marker, and a row in results/experiments/<timestamp>/summary.csv.
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reward.qos_reward import QoSReward, WEIGHT_CONFIGS

DEFAULT_CONFIG = ROOT / "experiments" / "config.yaml"
DEFAULT_CONTROLLERS = ["dijkstra", "rlearner", "roptimizer", "staged_controller"]
DEFAULT_TOPOLOGIES = ["abilene"]


@dataclass
class RunResult:
    run_id: str
    controller: str
    topology: str
    status: str
    rows: int
    expected_rows: Optional[int]
    avg_throughput_mbps: Optional[float]
    avg_jitter_ms: Optional[float]
    avg_loss_pct: Optional[float]
    avg_rtt_ms: Optional[float]
    weighted_reward_count: int
    avg_weighted_reward: Optional[float]
    traffic_csv: str
    run_dir: str
    error: str = ""


def load_config(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    with path.open() as handle:
        return yaml.safe_load(handle) or {}


def csv_list(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def bool_text(value: object) -> str:
    if isinstance(value, str):
        return "1" if value.strip().lower() in ("1", "true", "yes", "on") else "0"
    return "1" if bool(value) else "0"


def config_get(config: Dict[str, object], section: str, key: str, default):
    section_data = config.get(section, {})
    if isinstance(section_data, dict) and key in section_data:
        return section_data[key]
    return default


def split_matrix(value: object, default: Iterable[str]) -> List[str]:
    if isinstance(value, str):
        return csv_list(value) or list(default)
    if isinstance(value, list):
        return [str(item) for item in value]
    return list(default)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--controllers", help="Comma-separated controller names")
    parser.add_argument("--topologies", help="Comma-separated topology names")
    parser.add_argument("--results-dir")
    parser.add_argument("--name")
    parser.add_argument("--duration", type=int)
    parser.add_argument("--episodes", type=int)
    parser.add_argument("--pair-count", type=int)
    parser.add_argument("--pair-mode")
    parser.add_argument("--pairs")
    parser.add_argument("--protocols")
    parser.add_argument("--flows-per-pair", type=int)
    parser.add_argument("--interval", type=int)
    parser.add_argument("--link-bw-mbps", type=int)
    parser.add_argument("--stagger-min", type=float)
    parser.add_argument("--stagger-max", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--feedback-grace", type=float)
    parser.add_argument("--reward-mode", choices=["binary", "weighted"])
    parser.add_argument(
        "--weighted-profile",
        choices=sorted(WEIGHT_CONFIGS.keys()),
        help="QoS reward weights profile for weighted reward mode.",
    )
    parser.add_argument(
        "--staged-checkpoint",
        help="JSON checkpoint path used by staged_controller to load/save learning state.",
    )
    parser.add_argument(
        "--no-staged-checkpoint-autosave",
        action="store_true",
        help="Load checkpoint if present but do not save it at controller shutdown.",
    )
    parser.add_argument("--controller-start-wait", type=float)
    parser.add_argument("--topology-start-wait", type=float)
    parser.add_argument("--traffic-timeout-buffer", type=float)
    parser.add_argument("--stop-timeout", type=float)
    parser.add_argument("--cleanup-timeout", type=float)
    parser.add_argument("--ping", action="store_true", default=None)
    parser.add_argument("--no-ping", action="store_false", dest="ping")
    parser.add_argument("--verbose-traffic", action="store_true", default=None)
    parser.add_argument("--quiet-traffic", action="store_false", dest="verbose_traffic")
    parser.add_argument("--concurrent", action="store_true", default=None)
    parser.add_argument("--sequential", action="store_false", dest="concurrent")
    parser.add_argument("--fail-fast", action="store_true", default=None)
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def stream_to_log(pipe, log_path: Path) -> None:
    with log_path.open("w", buffering=1, errors="replace") as log:
        while True:
            chunk = pipe.readline()
            if not chunk:
                break
            log.write(chunk)


def start_logged_process(
    args: List[str],
    env: Dict[str, str],
    cwd: Path,
    stdout_path: Path,
    stdin=None,
) -> subprocess.Popen:
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
    )
    assert process.stdout is not None
    thread = threading.Thread(
        target=stream_to_log,
        args=(process.stdout, stdout_path),
        daemon=True,
    )
    thread.start()
    return process


def run_logged_command(
    args: List[str],
    env: Dict[str, str],
    cwd: Path,
    log_path: Path,
    timeout: float,
) -> int:
    with log_path.open("w", errors="replace") as log:
        process = subprocess.run(
            args,
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    return process.returncode


def cleanup_network(run_dir: Path, label: str, timeout: float) -> None:
    cleanup_env = os.environ.copy()
    commands = [
        (["sudo", "-n", "mn", "-c"], run_dir / f"cleanup_{label}_mininet.log"),
        (["pkill", "-f", "ryu-manager"], run_dir / f"cleanup_{label}_ryu.log"),
        (["pkill", "-f", "iperf"], run_dir / f"cleanup_{label}_iperf.log"),
        (["pkill", "-f", "iperf3"], run_dir / f"cleanup_{label}_iperf3.log"),
    ]
    for command, log_path in commands:
        try:
            code = run_logged_command(command, cleanup_env, ROOT, log_path, timeout)
        except FileNotFoundError as exc:
            log_path.write_text(f"{exc}\n")
            continue
        except subprocess.TimeoutExpired:
            log_path.write_text(f"timed out after {timeout} seconds\n")
            continue
        if command[0] == "sudo" and code != 0:
            raise RuntimeError(
                "Mininet cleanup requires passwordless or cached sudo. "
                "Run `sudo -v` first, or pass --skip-cleanup."
            )


def stop_process(process: Optional[subprocess.Popen], timeout: float, stdin_text: str = "") -> None:
    if process is None or process.poll() is not None:
        return
    if stdin_text and process.stdin is not None:
        try:
            process.stdin.write(stdin_text)
            process.stdin.flush()
        except BrokenPipeError:
            pass
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=timeout)


def traffic_env(args: argparse.Namespace, config: Dict[str, object], output: Path) -> Dict[str, str]:
    traffic = config.get("traffic", {})
    if not isinstance(traffic, dict):
        traffic = {}

    def pick(cli_name: str, key: str, default):
        value = getattr(args, cli_name)
        if value is not None:
            return value
        return traffic.get(key, default)

    pairs = pick("pairs", "pairs", "")
    env = {
        "TRAFFIC_PAIRS": str(pairs or ""),
        "TRAFFIC_PROTOCOLS": str(pick("protocols", "protocols", "both")),
        "TRAFFIC_PAIR_COUNT": str(pick("pair_count", "pair_count", 3)),
        "TRAFFIC_PAIR_MODE": str(pick("pair_mode", "pair_mode", "ends")),
        "TRAFFIC_FLOWS_PER_PAIR": str(pick("flows_per_pair", "flows_per_pair", 1)),
        "TRAFFIC_EPISODES": str(pick("episodes", "episodes", 1)),
        "TRAFFIC_DURATION": str(pick("duration", "duration", 60)),
        "TRAFFIC_INTERVAL": str(pick("interval", "interval", 1)),
        "TRAFFIC_LINK_BW_Mbps": str(pick("link_bw_mbps", "link_bw_mbps", 100)),
        "TRAFFIC_STAGGER_MIN": str(pick("stagger_min", "stagger_min", 1)),
        "TRAFFIC_STAGGER_MAX": str(pick("stagger_max", "stagger_max", 3)),
        "TRAFFIC_OUTPUT": str(output),
        "TRAFFIC_DONE": str(output) + ".done",
        "TRAFFIC_PING": bool_text(pick("ping", "ping", False)),
        "TRAFFIC_VERBOSE": bool_text(pick("verbose_traffic", "verbose", True)),
        "TRAFFIC_FEEDBACK_HOST": "127.0.0.1",
        "TRAFFIC_FEEDBACK_GRACE": str(pick("feedback_grace", "feedback_grace", 0.2)),
        "TRAFFIC_CONCURRENT": bool_text(pick("concurrent", "concurrent", False)),
    }
    if os.environ.get("TRAFFIC_UDP_BW_MBPS"):
        env["TRAFFIC_UDP_BW_MBPS"] = os.environ["TRAFFIC_UDP_BW_MBPS"]
    if os.environ.get("TRAFFIC_UDP_BW_RATIO"):
        env["TRAFFIC_UDP_BW_RATIO"] = os.environ["TRAFFIC_UDP_BW_RATIO"]
    if os.environ.get("TRAFFIC_CONCURRENT_START_SPREAD"):
        env["TRAFFIC_CONCURRENT_START_SPREAD"] = os.environ["TRAFFIC_CONCURRENT_START_SPREAD"]
    seed = pick("seed", "seed", None)
    if seed is not None and str(seed) != "":
        env["TRAFFIC_SEED"] = str(seed)
    return env


def controller_env(
    args: argparse.Namespace,
    config: Dict[str, object],
    topology: str,
    traffic_vars: Dict[str, str],
) -> Dict[str, str]:
    env = os.environ.copy()
    env["TOPOLOGY"] = topology
    controller_config = config.get("controller_env", {})
    if isinstance(controller_config, dict):
        for key, value in controller_config.items():
            # Shell environment is the experimenter's explicit override.
            # Keep config.yaml as defaults so sudo -E env ... commands are honored.
            env.setdefault(str(key), str(value))
    env["SKIP_MININET_CLEANUP"] = "1"
    env["NONINTERACTIVE_SUDO"] = "1"
    if args.reward_mode:
        env["REWARD_MODE"] = args.reward_mode
    if args.weighted_profile:
        env["WEIGHTED_PROFILE"] = args.weighted_profile
    if args.staged_checkpoint:
        env["STAGED_CHECKPOINT_PATH"] = str(Path(args.staged_checkpoint))
    if args.no_staged_checkpoint_autosave:
        env["STAGED_CHECKPOINT_AUTOSAVE"] = "0"
    env["TRAFFIC_PROTOCOLS"] = traffic_vars["TRAFFIC_PROTOCOLS"]
    if env.get("REWARD_MODE") == "weighted":
        env.setdefault("REWARD_FEEDBACK_HOST", traffic_vars["TRAFFIC_FEEDBACK_HOST"])
        env.setdefault("REWARD_FEEDBACK_PORT", "9999")
        traffic_vars["TRAFFIC_FEEDBACK_PORT"] = env["REWARD_FEEDBACK_PORT"]
    return env


def topology_env(base_env: Dict[str, str]) -> Dict[str, str]:
    env = base_env.copy()
    env.setdefault("NONINTERACTIVE_SUDO", "1")
    env.setdefault("SKIP_MININET_CLEANUP", "1")
    return env


def inject_traffic(
    topology_process: subprocess.Popen,
    traffic_vars: Dict[str, str],
    run_dir: Path,
) -> None:
    if topology_process.stdin is None:
        raise RuntimeError("Topology process stdin is unavailable")
    output = Path(traffic_vars["TRAFFIC_OUTPUT"])
    traffic_script = run_dir / "injected_traffic.py"
    lines = [
        "import os, sys, traceback",
        f"sys.path.insert(0, {str(ROOT)!r})",
    ]
    for key, value in traffic_vars.items():
        lines.append(f"os.environ[{key!r}] = {value!r}")
    lines.extend([
        "try:",
        "\t__import__('traffic.generate_traffic', fromlist=['run_from_env']).run_from_env(net)",
        "except BaseException:",
        f"\twith open({str(output) + '.partial'!r}, 'w') as handle:",
        "\t\thandle.write('complete=0\\n')",
        "\t\thandle.write('error=injected traffic exception\\n')",
        "\t\ttraceback.print_exc(file=handle)",
        "\traise",
    ])
    traffic_script.write_text("\n".join(lines) + "\n")
    topology_process.stdin.write(f"py exec(open({str(traffic_script)!r}).read())\n")
    topology_process.stdin.flush()


def write_partial_marker(output: Path, rows: int, expected_rows: int, error: str) -> None:
    partial = Path(str(output) + ".partial")
    if partial.exists() or Path(str(output) + ".done").exists():
        return
    with partial.open("w") as handle:
        handle.write(f"rows={rows}\n")
        handle.write(f"expected_rows={expected_rows}\n")
        handle.write("complete=0\n")
        handle.write(f"error={error}\n")


def count_csv_rows(output: Path) -> int:
    if not output.exists():
        return 0
    try:
        with output.open(newline="") as handle:
            return max(sum(1 for _line in handle) - 1, 0)
    except OSError:
        return 0


def wait_for_marker(
    output: Path,
    timeout: float,
    topology_process: Optional[subprocess.Popen] = None,
    controller_process: Optional[subprocess.Popen] = None,
    expected_rows: Optional[int] = None,
) -> str:
    done = Path(str(output) + ".done")
    partial = Path(str(output) + ".partial")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done.exists():
            return "complete"
        if partial.exists():
            return "partial"
        if topology_process is not None and topology_process.poll() is not None:
            rows = count_csv_rows(output)
            write_partial_marker(
                output,
                rows,
                expected_rows or 0,
                f"topology exited early with code {topology_process.returncode}",
            )
            return "topology_exit"
        if controller_process is not None and controller_process.poll() is not None:
            rows = count_csv_rows(output)
            write_partial_marker(
                output,
                rows,
                expected_rows or 0,
                f"controller exited early with code {controller_process.returncode}",
            )
            return "controller_exit"
        time.sleep(1)
    rows = count_csv_rows(output)
    write_partial_marker(
        output,
        rows,
        expected_rows or 0,
        f"timeout after {timeout:.1f} seconds",
    )
    return "timeout"


def marker_expected_rows(output: Path) -> Optional[int]:
    for suffix in (".done", ".partial"):
        marker = Path(str(output) + suffix)
        if not marker.exists():
            continue
        data = {}
        with marker.open() as handle:
            for line in handle:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    data[key] = value
        try:
            return int(data["expected_rows"])
        except (KeyError, ValueError):
            return None
    return None


def protocol_count(raw: str) -> int:
    raw = (raw or "both").lower()
    if raw == "both":
        return 2
    return len([item for item in raw.split(",") if item.strip()])


def expected_rows_from_env(traffic_vars: Dict[str, str]) -> int:
    pair_count = int(traffic_vars["TRAFFIC_PAIR_COUNT"])
    flows_per_pair = int(traffic_vars["TRAFFIC_FLOWS_PER_PAIR"])
    episodes = int(traffic_vars["TRAFFIC_EPISODES"])
    protocols = protocol_count(traffic_vars["TRAFFIC_PROTOCOLS"])
    pairs = [item for item in traffic_vars.get("TRAFFIC_PAIRS", "").split(",") if item.strip()]
    selected_pairs = len(pairs) if pairs else pair_count
    return episodes * selected_pairs * flows_per_pair * protocols


def estimate_traffic_timeout(traffic_vars: Dict[str, str], buffer_seconds: float) -> float:
    expected_rows = expected_rows_from_env(traffic_vars)
    duration = int(traffic_vars["TRAFFIC_DURATION"])
    stagger_max = float(traffic_vars["TRAFFIC_STAGGER_MAX"])
    episodes = int(traffic_vars["TRAFFIC_EPISODES"])
    rows_per_episode = max(expected_rows // max(episodes, 1), 1)
    ping_enabled = traffic_vars.get("TRAFFIC_PING") == "1"
    flow_runtime = max(duration, 22 if ping_enabled else duration)
    stagger_runtime = episodes * max(rows_per_episode - 1, 0) * stagger_max
    return expected_rows * flow_runtime + stagger_runtime + buffer_seconds


def average(values: Iterable[Optional[str]]) -> Optional[float]:
    nums = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            nums.append(float(value))
        except ValueError:
            continue
    if not nums:
        return None
    return sum(nums) / len(nums)


def weighted_rewards(rows: List[Dict[str, str]], profile: str, window: int) -> List[float]:
    grouped: Dict[tuple, Dict[str, float]] = {}
    for row in rows:
        key = (row.get("episode"), row.get("flow_id"), row.get("src"), row.get("dst"))
        metrics = grouped.setdefault(key, {})
        protocol = row.get("protocol")
        if protocol == "tcp" and row.get("throughput_mbps"):
            metrics["throughput_gbps"] = float(row["throughput_mbps"]) / 1000.0
        if protocol == "udp":
            if row.get("jitter_ms"):
                metrics["jitter_ms"] = float(row["jitter_ms"])
            if row.get("loss_pct"):
                metrics["plr_pct"] = float(row["loss_pct"])
        if row.get("rtt_ms"):
            current = metrics.get("rtt_ms")
            value = float(row["rtt_ms"])
            metrics["rtt_ms"] = value if current is None else (current + value) / 2.0

    reward_fn = QoSReward(
        weights=WEIGHT_CONFIGS.get(profile, WEIGHT_CONFIGS["balanced"]),
        window=window,
        normalization=os.environ.get("QOS_REWARD_NORMALIZATION", "absolute"),
    )
    rewards = []
    for _key, metrics in sorted(grouped.items()):
        if not {"throughput_gbps", "rtt_ms", "jitter_ms", "plr_pct"} <= set(metrics):
            continue
        rewards.append(reward_fn.compute_reward(metrics))
    return rewards


def summarize_csv(path: Path, profile: str = "balanced", window: int = 20) -> Dict[str, object]:
    if not path.exists():
        return {
            "rows": 0,
            "avg_throughput_mbps": None,
            "avg_jitter_ms": None,
            "avg_loss_pct": None,
            "avg_rtt_ms": None,
            "weighted_reward_count": 0,
            "avg_weighted_reward": None,
        }
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    rewards = weighted_rewards(rows, profile, window)
    return {
        "rows": len(rows),
        "avg_throughput_mbps": average(row.get("throughput_mbps") for row in rows),
        "avg_jitter_ms": average(row.get("jitter_ms") for row in rows),
        "avg_loss_pct": average(row.get("loss_pct") for row in rows),
        "avg_rtt_ms": average(row.get("rtt_ms") for row in rows),
        "weighted_reward_count": len(rewards),
        "avg_weighted_reward": average(str(reward) for reward in rewards),
    }


def run_one(
    run_index: int,
    controller: str,
    topology: str,
    args: argparse.Namespace,
    config: Dict[str, object],
    output_root: Path,
) -> RunResult:
    run_id = f"{run_index:02d}_{controller}_{topology}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    traffic_csv = run_dir / "traffic.csv"
    traffic_vars = traffic_env(args, config, traffic_csv)
    ctrl_env = controller_env(args, config, topology, traffic_vars)
    topo_env = topology_env(os.environ.copy())
    topo_env.update(traffic_vars)

    controller_process = None
    topology_process = None
    status = "failed"
    error = ""
    try:
        if not args.skip_cleanup:
            cleanup_network(run_dir, "before", float(args.cleanup_timeout))

        controller_process = start_logged_process(
            [str(ROOT / "scripts" / "run_controller.sh"), controller, topology],
            ctrl_env,
            ROOT,
            run_dir / "controller.log",
            stdin=subprocess.DEVNULL,
        )
        time.sleep(float(args.controller_start_wait))
        if controller_process.poll() is not None:
            raise RuntimeError(f"controller exited early with code {controller_process.returncode}")

        topology_process = start_logged_process(
            [str(ROOT / "scripts" / "run_topology.sh"), topology],
            topo_env,
            ROOT,
            run_dir / "topology.log",
            stdin=subprocess.PIPE,
        )
        time.sleep(float(args.topology_start_wait))
        if topology_process.poll() is not None:
            raise RuntimeError(f"topology exited early with code {topology_process.returncode}")

        inject_traffic(topology_process, traffic_vars, run_dir)
        timeout = estimate_traffic_timeout(
            traffic_vars,
            float(args.traffic_timeout_buffer)
        )
        expected_rows = expected_rows_from_env(traffic_vars)
        status = wait_for_marker(
            traffic_csv,
            timeout,
            topology_process=topology_process,
            controller_process=controller_process,
            expected_rows=expected_rows,
        )
        if status != "complete":
            error = f"traffic status={status}"
    except Exception as exc:
        error = str(exc)
    finally:
        stop_process(topology_process, float(args.stop_timeout), stdin_text="exit\n")
        stop_process(controller_process, float(args.stop_timeout))
        if not args.skip_cleanup:
            try:
                cleanup_network(run_dir, "after", float(args.cleanup_timeout))
            except RuntimeError as exc:
                if not error:
                    error = str(exc)

    profile = ctrl_env.get("WEIGHTED_PROFILE", "balanced")
    window = int(ctrl_env.get("BASELINE_WINDOW", "20"))
    summary = summarize_csv(traffic_csv, profile, window)
    if status == "complete" and error:
        status = "failed"
    return RunResult(
        run_id=run_id,
        controller=controller,
        topology=topology,
        status=status,
        rows=int(summary["rows"]),
        expected_rows=marker_expected_rows(traffic_csv) or expected_rows_from_env(traffic_vars),
        avg_throughput_mbps=summary["avg_throughput_mbps"],
        avg_jitter_ms=summary["avg_jitter_ms"],
        avg_loss_pct=summary["avg_loss_pct"],
        avg_rtt_ms=summary["avg_rtt_ms"],
        weighted_reward_count=int(summary["weighted_reward_count"]),
        avg_weighted_reward=summary["avg_weighted_reward"],
        traffic_csv=str(traffic_csv),
        run_dir=str(run_dir),
        error=error,
    )


def write_summary(path: Path, results: List[RunResult]) -> None:
    fieldnames = [
        "run_id",
        "controller",
        "topology",
        "status",
        "rows",
        "expected_rows",
        "avg_throughput_mbps",
        "avg_jitter_ms",
        "avg_loss_pct",
        "avg_rtt_ms",
        "weighted_reward_count",
        "avg_weighted_reward",
        "traffic_csv",
        "run_dir",
        "error",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result.__dict__)


def apply_defaults(args: argparse.Namespace, config: Dict[str, object]) -> None:
    args.results_dir = args.results_dir or str(
        config_get(config, "experiment", "results_dir", "results/experiments")
    )
    args.name = args.name or str(config_get(config, "experiment", "name", "run"))
    args.controller_start_wait = args.controller_start_wait or float(
        config_get(config, "experiment", "controller_start_wait", 4)
    )
    args.topology_start_wait = args.topology_start_wait or float(
        config_get(config, "experiment", "topology_start_wait", 8)
    )
    args.traffic_timeout_buffer = args.traffic_timeout_buffer or float(
        config_get(config, "experiment", "traffic_timeout_buffer", 45)
    )
    args.stop_timeout = args.stop_timeout or float(
        config_get(config, "experiment", "stop_timeout", 8)
    )
    args.cleanup_timeout = args.cleanup_timeout or float(
        config_get(config, "experiment", "cleanup_timeout", 60)
    )
    if not args.skip_cleanup:
        args.skip_cleanup = not bool(config_get(config, "experiment", "cleanup", True))
    if args.fail_fast is None:
        args.fail_fast = bool(config_get(config, "experiment", "fail_fast", False))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(Path(args.config))
    apply_defaults(args, config)

    matrix = config.get("matrix", {})
    if not isinstance(matrix, dict):
        matrix = {}
    controllers = csv_list(args.controllers) or split_matrix(
        matrix.get("controllers"), DEFAULT_CONTROLLERS
    )
    topologies = csv_list(args.topologies) or split_matrix(
        matrix.get("topologies"), DEFAULT_TOPOLOGIES
    )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = ROOT / args.results_dir / f"{timestamp}_{args.name}"
    output_root.mkdir(parents=True, exist_ok=True)

    print(f"[experiment] output: {output_root}")
    print(f"[experiment] controllers: {', '.join(controllers)}")
    print(f"[experiment] topologies: {', '.join(topologies)}")

    if args.dry_run:
        return 0

    results: List[RunResult] = []
    run_index = 1
    for topology in topologies:
        for controller in controllers:
            print(f"[experiment] run {run_index}: controller={controller} topology={topology}")
            result = run_one(run_index, controller, topology, args, config, output_root)
            results.append(result)
            write_summary(output_root / "summary.csv", results)
            print(
                f"[experiment] {result.run_id} status={result.status} "
                f"rows={result.rows}/{result.expected_rows} error={result.error}"
            )
            if args.fail_fast and result.status != "complete":
                return 1
            run_index += 1

    failures = [result for result in results if result.status != "complete"]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

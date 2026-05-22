#!/usr/bin/env python3

"""Generate QoS and weighted-learning plots for one experiment run."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reward.qos_reward import QoSReward, WEIGHT_CONFIGS


def latest_experiment() -> Path:
    candidates = sorted(
        (ROOT / "results" / "experiments").glob("*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if (path / "summary.csv").exists():
            return path
    raise FileNotFoundError("No experiment directory with summary.csv found")


def run_dir_from_summary(experiment_dir: Path) -> Path:
    summary = pd.read_csv(experiment_dir / "summary.csv")
    if summary.empty:
        raise ValueError(f"Empty summary: {experiment_dir / 'summary.csv'}")
    return Path(summary.iloc[0]["run_dir"])


def build_flow_metrics(
    traffic_csv: Path,
    profile: str,
    window: int,
    rtt_clip_ms: Optional[float] = None,
) -> pd.DataFrame:
    traffic = pd.read_csv(traffic_csv)
    traffic["timestamp"] = pd.to_datetime(traffic["timestamp"], utc=True)
    rows = []
    reward_fn = QoSReward(
        weights=WEIGHT_CONFIGS.get(profile, WEIGHT_CONFIGS["balanced"]),
        window=window,
        rtt_clip_ms=rtt_clip_ms,
    )

    for (_episode, flow_id, src, dst), group in traffic.groupby(
        ["episode", "flow_id", "src", "dst"],
        sort=True,
    ):
        tcp_rows = group[group["protocol"] == "tcp"]
        udp_rows = group[group["protocol"] == "udp"]
        if tcp_rows.empty or udp_rows.empty:
            continue

        throughput_mbps = float(tcp_rows["throughput_mbps"].dropna().mean())
        jitter_ms = float(udp_rows["jitter_ms"].dropna().mean())
        loss_pct = float(udp_rows["loss_pct"].dropna().mean())
        rtt_ms = float(group["rtt_ms"].dropna().mean())
        metrics = {
            "throughput_gbps": throughput_mbps / 1000.0,
            "rtt_ms": rtt_ms,
            "jitter_ms": jitter_ms,
            "plr_pct": loss_pct,
        }
        reward = reward_fn.compute_reward(metrics)
        rows.append({
            "episode": int(_episode),
            "flow_id": flow_id,
            "src": src,
            "dst": dst,
            "timestamp": group["timestamp"].max(),
            "throughput_mbps": throughput_mbps,
            "rtt_ms": rtt_ms,
            "jitter_ms": jitter_ms,
            "loss_pct": loss_pct,
            "weighted_reward": reward,
        })

    result = pd.DataFrame(rows).sort_values(["episode", "timestamp"]).reset_index(drop=True)
    result["decision"] = result.index + 1
    result["reward_rolling_mean"] = (
        result["weighted_reward"].rolling(window=5, min_periods=1).mean()
    )
    return result


def parse_controller_rewards(controller_log: Path) -> pd.DataFrame:
    if not controller_log.exists():
        return pd.DataFrame()
    pattern = re.compile(r"Live weighted QoS reward for (\S+) -> (\S+): ([+-]?\d+(?:\.\d+)?)")
    rows = []
    with controller_log.open(errors="replace") as handle:
        for line in handle:
            match = pattern.search(line)
            if not match:
                continue
            rows.append({
                "decision": len(rows) + 1,
                "src": match.group(1),
                "dst": match.group(2),
                "controller_weighted_reward": float(match.group(3)),
            })
    return pd.DataFrame(rows)


def write_episode_metrics(flow_metrics: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    episode_metrics = flow_metrics.groupby("episode", as_index=False).agg({
        "throughput_mbps": "mean",
        "rtt_ms": "mean",
        "jitter_ms": "mean",
        "loss_pct": "mean",
        "weighted_reward": "mean",
    })
    episode_metrics["reward_rolling_mean"] = (
        episode_metrics["weighted_reward"].rolling(window=3, min_periods=1).mean()
    )
    episode_metrics.to_csv(output_path, index=False)
    return episode_metrics


def plot(flow_metrics: pd.DataFrame, episode_metrics: pd.DataFrame, title: str, output_base: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5), constrained_layout=True)
    fig.suptitle(title, fontsize=14)

    ax = axes[0, 0]
    ax.plot(
        flow_metrics["decision"],
        flow_metrics["weighted_reward"],
        marker="o",
        linewidth=1.2,
        label="weighted reward",
    )
    ax.plot(
        flow_metrics["decision"],
        flow_metrics["reward_rolling_mean"],
        linewidth=2.2,
        label="5-flow mean",
    )
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.6)
    ax.set_title("Weighted Reward Per Completed Flow Group")
    ax.set_xlabel("Decision / flow group")
    ax.set_ylabel("Reward")
    ax.legend()

    ax = axes[0, 1]
    ax.plot(
        episode_metrics["episode"],
        episode_metrics["weighted_reward"],
        marker="o",
        label="episode mean",
    )
    ax.plot(
        episode_metrics["episode"],
        episode_metrics["reward_rolling_mean"],
        linewidth=2.0,
        label="3-episode mean",
    )
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.6)
    ax.set_title("Episode Reward Trend")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Reward")
    ax.legend()

    ax = axes[1, 0]
    ax.plot(
        episode_metrics["episode"],
        episode_metrics["throughput_mbps"],
        marker="o",
        color="#1f77b4",
    )
    ax.set_title("Mean TCP Throughput")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Mbps")

    ax = axes[1, 1]
    ax.plot(
        episode_metrics["episode"],
        episode_metrics["rtt_ms"],
        marker="o",
        label="RTT ms",
    )
    ax.plot(
        episode_metrics["episode"],
        episode_metrics["jitter_ms"],
        marker="s",
        label="UDP jitter ms",
    )
    ax.plot(
        episode_metrics["episode"],
        episode_metrics["loss_pct"],
        marker="^",
        label="UDP loss %",
    )
    ax.set_title("Delay, Jitter, and Loss")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Metric value")
    ax.legend()

    fig.savefig(output_base.with_suffix(".png"), dpi=180)
    fig.savefig(output_base.with_suffix(".pdf"))
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default="")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--profile", default="balanced", choices=sorted(WEIGHT_CONFIGS.keys()))
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--rtt-clip-ms", type=float, default=None)
    parser.add_argument("--output-dir", default=str(ROOT / "results" / "plots"))
    args = parser.parse_args()

    experiment_dir = Path(args.experiment_dir) if args.experiment_dir else latest_experiment()
    run_dir = Path(args.run_dir) if args.run_dir else run_dir_from_summary(experiment_dir)
    traffic_csv = run_dir / "traffic.csv"
    controller_log = run_dir / "controller.log"
    if not traffic_csv.exists():
        raise FileNotFoundError(traffic_csv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{experiment_dir.name}_{run_dir.name}"
    flow_metrics = build_flow_metrics(
        traffic_csv,
        args.profile,
        args.window,
        args.rtt_clip_ms,
    )
    if flow_metrics.empty:
        raise ValueError(f"No complete TCP/UDP flow groups found in {traffic_csv}")

    controller_rewards = parse_controller_rewards(controller_log)
    if not controller_rewards.empty and len(controller_rewards) == len(flow_metrics):
        flow_metrics = flow_metrics.merge(controller_rewards, on="decision", how="left")

    flow_csv = output_dir / f"{prefix}_flow_group_learning_metrics.csv"
    episode_csv = output_dir / f"{prefix}_episode_qos_metrics.csv"
    flow_metrics.to_csv(flow_csv, index=False)
    episode_metrics = write_episode_metrics(flow_metrics, episode_csv)

    plot(
        flow_metrics,
        episode_metrics,
        f"{run_dir.name} weighted QoS learning",
        output_dir / f"{prefix}_weighted_learning_curves",
    )
    print(flow_csv)
    print(episode_csv)
    print(output_dir / f"{prefix}_weighted_learning_curves.png")
    print(output_dir / f"{prefix}_weighted_learning_curves.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

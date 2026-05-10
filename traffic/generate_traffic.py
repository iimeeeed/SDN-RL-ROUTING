#!/usr/bin/env python3

"""
Traffic generation for Mininet topologies.

Run from Mininet CLI:
  mininet> py import sys; sys.path.insert(0, "/path/to/project"); __import__("traffic.generate_traffic", fromlist=["run"]).run(net)

Environment variables:
  TRAFFIC_PAIRS="h1-h5,h2-h4"
  TRAFFIC_PROTOCOLS="tcp|udp|both"
  TRAFFIC_PAIR_COUNT="3"
  TRAFFIC_PAIR_MODE="ends|random|all|east_west"
  TRAFFIC_FLOWS_PER_PAIR="1"
  TRAFFIC_EPISODES="1"
  TRAFFIC_DURATION="60"
  TRAFFIC_INTERVAL="1"
  TRAFFIC_LINK_BW_Mbps="100"
  TRAFFIC_STAGGER_MIN="1"
  TRAFFIC_STAGGER_MAX="3"
  TRAFFIC_OUTPUT="results/traffic.csv"
  TRAFFIC_PING="0"
  TRAFFIC_VERBOSE="1"
  TRAFFIC_SEED=""
"""

import argparse
import csv
import itertools
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import subprocess
except ImportError:
    subprocess = None

Pair = Tuple[str, str]

DEFAULT_TRAFFIC_EPISODES = 1


def host_sort_key(name: str) -> Tuple[int, str]:
    match = re.search(r"(\d+)", name)
    if not match:
        return (0, name)
    return (int(match.group(1)), name)


def parse_pairs(raw: Optional[str]) -> List[Pair]:
    if not raw:
        return []

    pairs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            left, right = item.split("-", 1)
        elif ":" in item:
            left, right = item.split(":", 1)
        else:
            raise ValueError(f"Invalid pair format: {item}")
        pairs.append((left.strip(), right.strip()))
    return pairs


def default_pairs(host_names: List[str], pair_count: int) -> List[Pair]:
    pairs = []
    left = 0
    right = len(host_names) - 1

    while left < right and len(pairs) < pair_count:
        pairs.append((host_names[left], host_names[right]))
        left += 1
        right -= 1

    return pairs


def host_switch_groups(net) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for host in net.hosts:
        switch_name = None
        for intf in host.intfList():
            link = getattr(intf, "link", None)
            if link is None:
                continue
            other_intf = link.intf2 if link.intf1 == intf else link.intf1
            other_node = other_intf.node if other_intf else None
            if other_node is not None and other_node.name.startswith("s"):
                switch_name = other_node.name
                break
        if switch_name:
            groups.setdefault(switch_name, []).append(host.name)
    return groups


def build_east_west_pairs(host_names: List[str], pair_count: int, net,
                          rng: random.Random) -> List[Pair]:
    groups = host_switch_groups(net)
    east_pairs: List[Pair] = []
    for hosts in groups.values():
        if len(hosts) < 2:
            continue
        east_pairs.extend(itertools.combinations(sorted(hosts), 2))

    all_pairs = list(itertools.combinations(host_names, 2))
    if pair_count <= 0:
        return list(east_pairs) if east_pairs else list(all_pairs)

    if len(east_pairs) >= pair_count:
        return rng.sample(list(east_pairs), pair_count)

    east_set = set(east_pairs)
    remaining = [pair for pair in all_pairs if pair not in east_set]
    needed = pair_count - len(east_pairs)
    if not remaining:
        return list(east_pairs)

    if needed <= len(remaining):
        extra = rng.sample(remaining, needed)
    else:
        extra = [rng.choice(remaining) for _ in range(needed)]

    return list(east_pairs) + extra


def build_pairs(host_names: List[str], pair_count: int, mode: str,
                rng: random.Random, net=None) -> List[Pair]:
    if len(host_names) < 2:
        return []

    mode = (mode or "ends").lower()
    if mode == "ends":
        return default_pairs(host_names, pair_count)

    if mode in ("east_west", "edge"):
        if net is None:
            raise ValueError("pair_mode east_west requires a Mininet net object")
        return build_east_west_pairs(host_names, pair_count, net, rng)

    all_pairs = list(itertools.combinations(host_names, 2))
    if mode == "all":
        if pair_count <= 0 or pair_count >= len(all_pairs):
            return list(all_pairs)
        return rng.sample(all_pairs, pair_count)

    if mode == "random":
        if pair_count <= 0:
            return []
        if pair_count <= len(all_pairs):
            return rng.sample(all_pairs, pair_count)
        return [rng.choice(all_pairs) for _ in range(pair_count)]

    raise ValueError(f"Unknown pair mode: {mode}")


def parse_protocols(raw: str) -> List[str]:
    raw = (raw or "both").lower()
    if raw == "both":
        return ["tcp", "udp"]
    if raw in ("tcp", "udp"):
        return [raw]
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    normalized = []
    for part in parts:
        if part not in ("tcp", "udp"):
            raise ValueError(f"Unknown protocol: {part}")
        normalized.append(part)
    return normalized


def parse_tcp_throughput(output: str) -> Optional[float]:
    matches = re.findall(r"(\d+\.?\d*)\s+([KMG])bits/sec", output)
    if not matches:
        return None

    value, unit = matches[-1]
    value = float(value)
    if unit == "K":
        return value / 1000.0
    if unit == "M":
        return value
    if unit == "G":
        return value * 1000.0
    return None


def parse_udp_metrics(output: str) -> Tuple[Optional[float], Optional[float]]:
    match = re.search(r"(\d+\.?\d*)\s+ms\s+\d+/\d+\s+\((\d+\.?\d*)%\)", output)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def parse_rtt(output: str) -> Optional[float]:
    match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", output)
    if not match:
        return None
    return float(match.group(1))


def start_server(host, protocol: str, port: int):
    args = ["iperf", "-s", "-p", str(port)]
    if protocol == "udp":
        args.insert(2, "-u")

    return host.popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


def start_client(host, dst_ip: str, protocol: str, port: int, duration: int,
                 interval: int, udp_bw_mbps: int):
    args = [
        "iperf",
        "-c",
        dst_ip,
        "-t",
        str(duration),
        "-i",
        str(interval),
        "-f",
        "m",
        "-p",
        str(port)
    ]

    if protocol == "udp":
        args.extend(["-u", "-b", f"{udp_bw_mbps}M"])

    return host.popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )


def run(net,
        pairs: Optional[List[Pair]] = None,
        pair_count: int = 3,
        pair_mode: str = "ends",
        flows_per_pair: int = 1,
        episodes: int = DEFAULT_TRAFFIC_EPISODES,
        protocols: str = "both",
        duration: int = 60,
        interval: int = 1,
        link_bw_mbps: int = 100,
        stagger_min: float = 1.0,
        stagger_max: float = 3.0,
        output_path: Optional[str] = None,
        ping: bool = False,
        verbose: bool = True,
        seed: Optional[int] = None) -> List[Dict[str, object]]:
    """Run traffic batches against an existing Mininet network.

    One traffic episode is one complete pass over the selected flows. If
    episodes=3, each selected flow is run once in each of three batches.
    """
    if subprocess is None:
        raise RuntimeError("subprocess module is required for traffic generation")

    if flows_per_pair < 1:
        raise ValueError("flows_per_pair must be >= 1")

    if episodes < 1:
        raise ValueError("episodes must be >= 1")

    rng = random.Random(seed) if seed is not None else random

    host_names = sorted([host.name for host in net.hosts], key=host_sort_key)
    selected_pairs = pairs or build_pairs(host_names, pair_count, pair_mode, rng, net)
    selected_protocols = parse_protocols(protocols)

    if verbose:
        print("[traffic] hosts:", ", ".join(host_names))
        print("[traffic] pairs:", selected_pairs)
        print(
            "[traffic] pair_mode:",
            pair_mode,
            "flows_per_pair:",
            flows_per_pair,
            "episodes:",
            episodes,
            "seed:",
            seed
        )
        print("[traffic] protocols:", ", ".join(selected_protocols))
        print("[traffic] duration_s:", duration, "interval_s:", interval)

    if not selected_pairs:
        raise ValueError("No host pairs available for traffic generation")

    udp_bw_mbps = max(int(link_bw_mbps * 0.9), 1)

    flows = []
    tcp_index = 0
    udp_index = 0

    for src, dst in selected_pairs:
        for _ in range(flows_per_pair):
            if "tcp" in selected_protocols:
                flows.append({
                    "src": src,
                    "dst": dst,
                    "protocol": "tcp",
                    "port": 5001 + tcp_index
                })
                tcp_index += 1
            if "udp" in selected_protocols:
                flows.append({
                    "src": src,
                    "dst": dst,
                    "protocol": "udp",
                    "port": 6001 + udp_index
                })
                udp_index += 1

    servers = []
    for flow in flows:
        dst_host = net.get(flow["dst"])
        server = start_server(dst_host, flow["protocol"], flow["port"])
        flow["server"] = server
        servers.append(server)
        if verbose:
            print(
                f"[traffic] server {flow['protocol']} {flow['dst']} port={flow['port']}"
            )

    results = []
    for episode_index in range(episodes):
        if verbose:
            print(f"[traffic] starting episode {episode_index + 1}/{episodes}")

        episode_flows = list(flows)
        rng.shuffle(episode_flows)

        for index, flow in enumerate(episode_flows):
            if index > 0:
                time.sleep(rng.uniform(stagger_min, stagger_max))

            src_host = net.get(flow["src"])
            dst_ip = net.get(flow["dst"]).IP()

            client = start_client(
                src_host,
                dst_ip,
                flow["protocol"],
                flow["port"],
                duration,
                interval,
                udp_bw_mbps
            )
            flow["client"] = client
            if verbose:
                print(
                    f"[traffic] client {flow['protocol']} {flow['src']}->{flow['dst']} "
                    f"port={flow['port']}"
                )

        for flow in episode_flows:
            stdout, stderr = flow["client"].communicate()
            record = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "episode": episode_index + 1,
                "src": flow["src"],
                "dst": flow["dst"],
                "protocol": flow["protocol"],
                "port": flow["port"],
                "duration_s": duration
            }

            if stderr:
                record["stderr"] = stderr.strip()

            if flow["protocol"] == "tcp":
                record["throughput_mbps"] = parse_tcp_throughput(stdout)
            else:
                jitter_ms, loss_pct = parse_udp_metrics(stdout)
                record["jitter_ms"] = jitter_ms
                record["loss_pct"] = loss_pct

            if ping:
                ping_output = net.get(flow["src"]).cmd(
                    f"ping -c 20 {net.get(flow['dst']).IP()}"
                )
                record["rtt_ms"] = parse_rtt(ping_output)

            results.append(record)
            if verbose:
                if flow["protocol"] == "tcp":
                    print(
                        "[traffic] result",
                        flow["src"],
                        "->",
                        flow["dst"],
                        "tcp throughput_mbps=",
                        record.get("throughput_mbps")
                    )
                else:
                    print(
                        "[traffic] result",
                        flow["src"],
                        "->",
                        flow["dst"],
                        "udp jitter_ms=",
                        record.get("jitter_ms"),
                        "loss_pct=",
                        record.get("loss_pct")
                    )

    for server in servers:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=2)
            except Exception:
                server.kill()

    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        fieldnames = sorted({key for row in results for key in row.keys()})
        with open(output_path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        if verbose:
            print("[traffic] wrote", output_path)

    return results


def env_default(key: str, fallback: str) -> str:
    value = os.environ.get(key)
    return value if value is not None else fallback


def env_bool(key: str, fallback: bool = False) -> bool:
    value = os.environ.get(key)
    if value is None:
        return fallback
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int_optional(key: str) -> Optional[int]:
    value = os.environ.get(key)
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return int(value)


def run_from_env(net) -> List[Dict[str, object]]:
    pairs = parse_pairs(env_default("TRAFFIC_PAIRS", ""))
    output_path = env_default("TRAFFIC_OUTPUT", "") or None

    return run(
        net=net,
        pairs=pairs if pairs else None,
        pair_count=int(env_default("TRAFFIC_PAIR_COUNT", "3")),
        pair_mode=env_default("TRAFFIC_PAIR_MODE", "ends"),
        flows_per_pair=int(env_default("TRAFFIC_FLOWS_PER_PAIR", "1")),
        episodes=int(env_default("TRAFFIC_EPISODES", str(DEFAULT_TRAFFIC_EPISODES))),
        protocols=env_default("TRAFFIC_PROTOCOLS", "both"),
        duration=int(env_default("TRAFFIC_DURATION", "60")),
        interval=int(env_default("TRAFFIC_INTERVAL", "1")),
        link_bw_mbps=int(env_default("TRAFFIC_LINK_BW_Mbps", "100")),
        stagger_min=float(env_default("TRAFFIC_STAGGER_MIN", "1")),
        stagger_max=float(env_default("TRAFFIC_STAGGER_MAX", "3")),
        output_path=output_path,
        ping=env_bool("TRAFFIC_PING", False),
        verbose=env_bool("TRAFFIC_VERBOSE", True),
        seed=env_int_optional("TRAFFIC_SEED")
    )


def main(net=None) -> int:
    net = net or globals().get("net")

    parser = argparse.ArgumentParser(description="Generate iPerf traffic in Mininet")
    parser.add_argument("--pairs", default=env_default("TRAFFIC_PAIRS", ""))
    parser.add_argument("--protocols", default=env_default("TRAFFIC_PROTOCOLS", "both"))
    parser.add_argument("--pair-count", type=int, default=int(env_default("TRAFFIC_PAIR_COUNT", "3")))
    parser.add_argument("--pair-mode", default=env_default("TRAFFIC_PAIR_MODE", "ends"))
    parser.add_argument("--flows-per-pair", type=int, default=int(env_default("TRAFFIC_FLOWS_PER_PAIR", "1")))
    parser.add_argument("--episodes", type=int, default=int(env_default("TRAFFIC_EPISODES", str(DEFAULT_TRAFFIC_EPISODES))))
    parser.add_argument("--duration", type=int, default=int(env_default("TRAFFIC_DURATION", "60")))
    parser.add_argument("--interval", type=int, default=int(env_default("TRAFFIC_INTERVAL", "1")))
    parser.add_argument("--link-bw-mbps", type=int, default=int(env_default("TRAFFIC_LINK_BW_Mbps", "100")))
    parser.add_argument("--stagger-min", type=float, default=float(env_default("TRAFFIC_STAGGER_MIN", "1")))
    parser.add_argument("--stagger-max", type=float, default=float(env_default("TRAFFIC_STAGGER_MAX", "3")))
    parser.add_argument("--output", default=env_default("TRAFFIC_OUTPUT", ""))
    parser.add_argument("--ping", action="store_true", default=env_bool("TRAFFIC_PING", False))
    parser.add_argument("--verbose", action="store_true", default=env_bool("TRAFFIC_VERBOSE", True))
    parser.add_argument("--seed", type=int, default=env_int_optional("TRAFFIC_SEED"))

    if net is None:
        args = parser.parse_args()
        print("This script expects a Mininet net object. Use it from the Mininet CLI.")
        print('Example: mininet> py __import__("traffic.generate_traffic", fromlist=["run"]).run(net)')
        return 2

    args = parser.parse_args([])

    pairs = parse_pairs(args.pairs)
    output_path = args.output or None

    run(
        net=net,
        pairs=pairs if pairs else None,
        pair_count=args.pair_count,
        pair_mode=args.pair_mode,
        flows_per_pair=args.flows_per_pair,
        episodes=args.episodes,
        protocols=args.protocols,
        duration=args.duration,
        interval=args.interval,
        link_bw_mbps=args.link_bw_mbps,
        stagger_min=args.stagger_min,
        stagger_max=args.stagger_max,
        output_path=output_path,
        ping=args.ping,
        verbose=args.verbose,
        seed=args.seed
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

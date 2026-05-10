# Traffic Generation

This folder contains a small helper for generating repeatable traffic with iPerf
inside Mininet.

## Prerequisites
- Mininet running with a topology script from this repo.
- iPerf installed on the host (the script calls `iperf` inside Mininet hosts).

## Start the topology
From the project root:

```bash
sudo python topologies/abilene.py
# or
sudo python topologies/fat_tree.py
```

## Run traffic from the Mininet CLI
In the Mininet CLI prompt:

```text
mininet> py import sys; sys.path.insert(0, "/home/johndoe/Desktop/sdn-rl-routing"); __import__("traffic.generate_traffic", fromlist=["run"]).run(net)
```

This uses default settings:
- 3 host pairs (paired from ends of the host list)
- both TCP and UDP flows
- 1 traffic episode
- 60s duration, 1s report interval
- UDP bandwidth = 90% of link_bw_mbps (default 100 Mbps)
- staggered starts between 1-3 seconds

One traffic episode means one complete batch of the selected flows. If
`TRAFFIC_EPISODES=3`, each selected flow is run once in each of three batches.

## Customize runs (CLI)

```text
mininet> py import sys; sys.path.insert(0, "/home/johndoe/Desktop/sdn-rl-routing"); __import__("traffic.generate_traffic", fromlist=["run"]).run(net, pairs=[("h1","h5")], protocols="tcp", duration=30, episodes=1, output_path="results/traffic.csv")
```

## Customize via environment variables

```bash
export TRAFFIC_PAIRS="h1-h5,h2-h4"
export TRAFFIC_PROTOCOLS="both"   # tcp, udp, or both
export TRAFFIC_DURATION="60"
export TRAFFIC_EPISODES="1"
export TRAFFIC_INTERVAL="1"
export TRAFFIC_PAIR_MODE="ends"   # ends, random, all, east_west
export TRAFFIC_FLOWS_PER_PAIR="1"
export TRAFFIC_LINK_BW_Mbps="100"
export TRAFFIC_STAGGER_MIN="1"
export TRAFFIC_STAGGER_MAX="3"
export TRAFFIC_OUTPUT="results/traffic.csv"
export TRAFFIC_PING="0"           # 1 to also record ping RTT
export TRAFFIC_SEED=""             # set for repeatable random pairs
```

Then in Mininet CLI:

```text
mininet> py import sys; sys.path.insert(0, "/home/johndoe/Desktop/sdn-rl-routing"); __import__("traffic.generate_traffic", fromlist=["run_from_env"]).run_from_env(net)
```

## Output
If `TRAFFIC_OUTPUT` is set, results are written as CSV. Each row contains:
- episode, src, dst, protocol, port, duration_s
- throughput_mbps (TCP)
- jitter_ms and loss_pct (UDP)
- rtt_ms (if ping enabled)

# SDN Routing with Reinforcement Learning

This project is a research framework for comparing conventional shortest-path routing with reinforcement-learning-based routing in Software-Defined Networks. It combines Mininet topologies, a Ryu OpenFlow controller, reusable reward functions, and multiple exploration schedules in a structure intended for controlled routing experiments.

The immediate working baseline is topology-aware Dijkstra routing. The reinforcement-learning layer is partially prepared: QoS rewards and epsilon schedules are implemented, while the RL controller, route optimizer, experiment runner, configuration, and result analysis still need to be connected.

The project is therefore best understood as both a functional SDN routing baseline and a foundation for a larger RL study—not yet a finished RL routing system.

## Research goal

Static shortest-path routing is predictable and inexpensive, but it does not react to congestion, delay, jitter, or packet loss. The project is structured around a broader question:

> Can an SDN controller learn routing decisions that improve application-level QoS under changing network conditions?

The intended comparison includes:

- Dijkstra as the deterministic baseline;
- RL routing with fixed, linearly decaying, and exponentially decaying exploration;
- a simple success/failure reward versus a continuous QoS-aware reward;
- a smaller backbone topology and a denser, more complex topology;
- throughput, RTT, jitter, packet loss, convergence, and route stability.

## System design

```text
Traffic and link conditions
          │
          ▼
   Mininet + Open vSwitch
          │ OpenFlow 1.3
          ▼
      Ryu controller
      ├── Dijkstra routing              implemented
      ├── RL routing policy             planned
      ├── exploration schedule          implemented
      └── reward computation            implemented
          │
          ▼
  Flow rules installed in switches
          │
          ▼
 ping / iPerf3 measurements and analysis
```

The topology definition is intentionally separated from controller metadata:

- `topologies/` creates the live Mininet network.
- `topology_data/` gives controllers a graph, static host identities, and the OpenFlow port map.

This avoids coupling routing logic directly to Mininet objects. It also makes topology validation important: link order determines switch port numbers, so the runtime topology and controller metadata must remain synchronized.

## Implemented components

| Component | Status | Purpose |
|---|---|---|
| Abilene-inspired topology | Working | Smaller redundant backbone for baseline experiments |
| Reduced Fat Tree-like topology | Working | Dense topology for higher path diversity |
| Dijkstra controller | Working | Static weighted shortest-path routing |
| OpenFlow rule installation | Working | Installs bidirectional destination-MAC rules |
| Static ARP resolution | Working | Routes known hosts without uncontrolled flooding |
| Topology validator | Working | Checks node counts, metadata links, and port coverage |
| Binary reward | Working | Returns `+10` for success and `-10` for failure |
| QoS reward | Working | Combines throughput, RTT, jitter, and packet loss |
| Fixed epsilon | Working | Constant exploration rate |
| Linear epsilon decay | Working | Gradually reduces exploration to a floor |
| Exponential epsilon decay | Working | Front-loads exploration, then converges |
| RL controller | Scaffold | `controllers/rlearner.py` is currently empty |
| Route optimizer | Scaffold | `controllers/roptimizer.py` is currently empty |
| Experiment automation | Scaffold | Runner and YAML configuration are currently empty |
| Analysis pipeline | Scaffold | No result aggregation or plotting is implemented yet |

## Topologies

### Abilene-inspired backbone

The smaller topology contains seven OpenFlow switches and five hosts. Hosts connect at 100 Mbps with 1 ms delay, while backbone links use 1 Gbps and 3 ms.

| Property | Value |
|---|---:|
| Switches | 7 |
| Hosts | 5 |
| Backbone links | 14 |
| Host links | 5 |
| Total runtime links | 19 |
| Runtime TCI | 0.571 |

It is inspired by Abilene as a compact redundant backbone, but it is not a geographical reproduction of the historical network.

### Reduced Fat Tree-like topology

The larger topology contains eight switches and twelve hosts. Four switches act as host-facing nodes, with three hosts attached to each. Switch links use 1 Gbps and 2 ms; host links use 100 Mbps and 1 ms.

| Property | Value |
|---|---:|
| Switches | 8 |
| Hosts | 12 |
| Unique switch links at runtime | 28 |
| Host links | 12 |
| Total runtime links | 40 |
| Runtime TCI | 0.750 |

This is deliberately a reduced, densely connected Fat Tree-like test network. It is not a canonical k-ary Fat Tree.

## Dijkstra routing baseline

`controllers/dijkstra.py` loads the topology selected through the `TOPOLOGY` environment variable and constructs a weighted undirected NetworkX graph. When a known host sends ARP or unicast traffic, the controller:

1. maps source and destination addresses to static host identities;
2. finds their attachment switches;
3. computes the weighted shortest switch path;
4. obtains each output port from the topology port map;
5. installs the forward and reverse OpenFlow paths;
6. forwards the triggering packet immediately.

Rules match the destination MAC address, use priority 10, and expire after 60 seconds of inactivity. A table-miss rule sends unknown packets to the controller.

The controller intentionally avoids ordinary broadcast flooding because both topologies contain loops. Only hosts declared in `topology_data` are routable.

## RL building blocks

### Reward functions

The binary reward is useful as a minimal baseline:

```text
successful episode  → +10
failed episode      → -10
```

The richer reward function combines four live QoS measurements:

```text
rₜ = w₁·throughput − w₂·RTT − w₃·jitter − w₄·packet_loss
```

Each metric is min-max normalized over a rolling window, which keeps differently scaled measurements comparable. The default window contains 20 episodes, and three weight profiles are provided:

| Profile | Throughput | RTT | Jitter | Packet loss |
|---|---:|---:|---:|---:|
| Throughput prioritised | 0.50 | 0.20 | 0.15 | 0.15 |
| Balanced | 0.40 | 0.30 | 0.15 | 0.15 |
| Delay prioritised | 0.25 | 0.40 | 0.20 | 0.15 |

Zero throughput is treated as a failed path and returns `-1`. The class validates input metrics and weights, records reward history, and can reset state between independent runs.

### Exploration schedules

Three epsilon strategies are ready for an epsilon-greedy agent:

| Strategy | Default behavior |
|---|---|
| Fixed | `ε = 0.10` |
| Linear | Starts at `0.40`, subtracts `0.0035` per episode, floor `0.05` |
| Exponential | Starts at `0.50`, decay constant `0.05`, floor `0.05` |

The fixed schedule provides a stable control condition. Linear decay is easy to interpret, while exponential decay explores more heavily at the beginning and becomes conservative faster.

## Requirements

The project targets a Linux environment because Mininet and Open vSwitch require Linux networking features and root privileges.

The recorded development environment is:

- Python 3.9.19
- Ryu 4.34
- eventlet 0.30.2
- Mininet 2.3.0
- Open vSwitch 3.3.4
- iPerf3 3.16

Ubuntu, a Linux virtual machine, or a dedicated Mininet VM is recommended. A normal Windows Python environment cannot run the network emulation layer directly.

## Installation

Install the system networking tools on Ubuntu:

```bash
sudo apt update
sudo apt install mininet openvswitch-switch iperf3
```

From the project root, create the expected Python environment:

```bash
python3.9 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip<23"
python -m pip install -r requirements.txt
```

The old Ryu/eventlet combination is sensitive to Python and packaging-tool versions. The pins in `requirements.txt` are intentional; using a newer Python or eventlet version may require compatibility changes.

Check the installed toolchain:

```bash
bash scripts/check_versions.sh
```

## Running the Dijkstra baseline

Run commands from the repository root.

### 1. Validate controller metadata

```bash
python scripts/validate_topologies.py
```

This checks the metadata node and link counts and ensures every declared host and backbone neighbor has a corresponding port-map entry.

### 2. Start the Ryu controller

For Abilene:

```bash
TOPOLOGY=abilene ryu-manager controllers/dijkstra.py
```

For the Fat Tree-like topology:

```bash
TOPOLOGY=fat_tree ryu-manager controllers/dijkstra.py
```

The topology scripts expect the controller at `127.0.0.1:6633` and use OpenFlow 1.3.

### 3. Start Mininet

In a second terminal:

```bash
sudo python3 topologies/abilene.py
```

or:

```bash
sudo python3 topologies/fat_tree.py
```

Wait until all switches appear in the Ryu log before testing traffic.

### 4. Test routing

From the Mininet CLI:

```text
mininet> nodes
mininet> links
mininet> pingall
```

Test a specific route:

```text
mininet> h1 ping -c 5 h5
```

The first packet can be slower because it triggers controller processing and flow installation.

### 5. Measure QoS

TCP throughput:

```text
mininet> h5 iperf3 -s &
mininet> h1 iperf3 -c 10.0.0.5 -t 10
```

UDP throughput, jitter, and loss:

```text
mininet> h5 iperf3 -s &
mininet> h1 iperf3 -c 10.0.0.5 -u -b 50M -t 10
```

Use `ping` output for RTT and iPerf3 output for throughput, jitter, and packet loss. These values match the input contract expected by `QoSReward`.

After an interrupted run, clean stale Mininet, Ryu, and iPerf3 processes:

```bash
bash scripts/clean_mininet.sh
```

## Using the reward and exploration modules

The RL utilities can be exercised independently of Mininet:

```python
from reward.qos_reward import QoSReward, WEIGHT_CONFIGS
from exploration.exp_decay import ExponentialDecay

reward_fn = QoSReward(
    weights=WEIGHT_CONFIGS["balanced"],
    window=20,
)
schedule = ExponentialDecay()

episode = 12
epsilon = schedule.get_epsilon(episode)

reward = reward_fn.compute_reward({
    "throughput_gbps": 0.82,
    "rtt_ms": 7.4,
    "jitter_ms": 0.6,
    "plr_pct": 0.2,
})

print({"episode": episode, "epsilon": epsilon, "reward": reward})
```

An eventual RL controller should reset the reward window between experimental repetitions to prevent measurements from one run affecting another.

## Repository structure

```text
SDN-RL-ROUTING/
├── controllers/
│   ├── dijkstra.py            # Working OpenFlow 1.3 baseline
│   ├── rlearner.py            # RL controller scaffold
│   └── roptimizer.py          # Route-optimization scaffold
├── topologies/
│   ├── abilene.py             # Live Mininet topology
│   └── fat_tree.py
├── topology_data/
│   ├── abilene.py             # Controller graph and port map
│   └── fat_tree.py
├── reward/
│   ├── binary_reward.py
│   └── qos_reward.py
├── exploration/
│   ├── fixed_epsilon.py
│   ├── linear_decay.py
│   └── exp_decay.py
├── experiments/
│   ├── config.yaml            # Planned experiment configuration
│   └── run_experiment.py      # Planned automation entry point
├── analysis/                  # Planned result analysis
├── scripts/
│   ├── validate_topologies.py
│   ├── check_versions.sh
│   └── clean_mininet.sh
├── docs/SETUP.md
└── requirements.txt
```

## Current limitations

- **The RL loop is not implemented.** There is no state encoder, action space, Q-table or neural policy, training loop, checkpointing, or OpenFlow integration for learned routes.
- **Experiments are not automated.** Traffic generation, link perturbation, metric collection, repeated seeds, result storage, and plots must currently be handled manually.
- **Topology sources are not fully synchronized.** The Fat Tree metadata contains 44 link entries, including repeated switch pairs, while Mininet creates 28 unique switch links. `networkx.Graph` collapses repeated pairs, and the validator currently checks metadata internally rather than comparing it with the runtime topology.
- **Published topology comments are stale.** Some source docstrings mention 15 or 36 total links and TCI values of 0.58 or 0.85. The runtime structures currently produce 19 Abilene links, 40 Fat Tree-like links, and TCI values of approximately 0.571 and 0.750.
- **The baseline is not congestion-aware.** All Abilene graph weights are equal. Fat Tree metadata uses static weights, but no controller process updates them from live port statistics.
- **Only declared hosts are supported.** Unknown MAC or IP addresses are ignored, and there is no host-learning or general ARP proxy.
- **Failures are not handled dynamically.** The graph is loaded once; link-down events do not remove edges or trigger route recomputation.
- **Flow matching is coarse.** Rules match destination MAC only, without traffic class, source, transport protocol, or QoS policy.
- **No formal test suite exists.** The topology validation script is useful, but reward functions, exploration schedules, controller behavior, and failure cases do not have automated tests.
- **The dependency stack is old and fragile.** Ryu 4.34 and eventlet 0.30.2 constrain the usable Python environment.

The strongest next milestone is to reconcile topology metadata with the live Mininet graphs, then implement a small tabular Q-learning controller with a precise state/action definition and a reproducible experiment runner. That would turn the existing reward and exploration work into an end-to-end RL routing study.

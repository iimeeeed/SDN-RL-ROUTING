# SDN RL Routing Project

This project provides a platform for experimenting with Software Defined Networking (SDN) routing algorithms, including Dijkstra and reinforcement learning-based approaches, using the Ryu controller and Mininet network emulator.

## Features
- **Custom Topologies:** Includes Abilene and Fat Tree topologies with adjustable complexity and redundancy.
- **Dijkstra Controller:** Implements shortest-path routing using the Dijkstra algorithm.
- **Reinforcement Learning Controller:** (Optional) For advanced routing experiments.
- **Validation Scripts:** Ensures topologies and port maps are consistent and correct.
- **Performance Testing:** Use iPerf3 to measure network throughput and latency.


## Quick Start
1. **Set up your environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Validate topologies:**
   ```bash
   python scripts/validate_topologies.py
   ```
3. **Start Mininet with a topology:**
   ```bash
   sudo python topologies/abilene.py
   # or
   sudo python topologies/fat_tree.py
   ```
   Or use the helper script (auto activates venv and runs `sudo mn -c`):
   ```bash
   scripts/run_topology.sh abilene
   # or
   scripts/run_topology.sh fat_tree
   ```
4. **Run the Dijkstra controller:**
   ```bash
   TOPOLOGY=abilene ryu-manager controllers/dijkstra.py
   # or
   TOPOLOGY=fat_tree ryu-manager controllers/dijkstra.py
   ```
   Or use the helper script:
   ```bash
   scripts/run_controller.sh dijkstra abilene
   # or
   scripts/run_controller.sh dijkstra fat_tree
   ```
5. **Test connectivity and performance:**
   Use Mininet CLI commands like `pingall` and `iperf`.

## Project Structure
- `controllers/` — SDN controller implementations
- `topologies/` — Mininet topology scripts
- `topology_data/` — Data files for topologies (used by controllers)
- `scripts/` — Validation and utility scripts
- `report/`, `results/` — For experiment outputs and analysis

## Helper Scripts
The scripts in `scripts/` activate `.mnvenv` (or `.venv`) automatically and
run `sudo mn -c` at the start of each run to clear old Mininet state. You can
override the venv with `VENV_PATH=/path/to/activate`.

### Run the controller
```bash
scripts/run_controller.sh dijkstra abilene
scripts/run_controller.sh rlearner abilene_imp
EXPLORE_DECISIONS=50 scripts/run_controller.sh staged_controller abilene
```

### Run the topology
```bash
scripts/run_topology.sh abilene
scripts/run_topology.sh fat_tree
scripts/run_topology.sh abilene_imp
```

Topology startup is decoupled from traffic generation. It only starts the
Mininet network and opens the CLI; run traffic separately from the Mininet CLI
or a third terminal.

From the Mininet CLI:
```text
mininet> py import sys; sys.path.insert(0, "/home/johndoe/Desktop/sdn-rl-routing"); __import__("traffic.generate_traffic", fromlist=["run"]).run(net, pair_count=2, protocols="both", duration=10, episodes=1, output_path="results/traffic.csv")
```

One traffic episode means one complete batch of the selected flows. For example,
`TRAFFIC_EPISODES=3` runs the same selected flow set in three separate batches.

Controllers install learned forwarding rules with `LEARNED_FLOW_IDLE_TIMEOUT=1`
by default so repeated traffic episodes can trigger fresh routing decisions.
Increase it for steadier forwarding, or decrease it when you want more frequent
controller decisions during experiments.

Use `--reward-mode binary` for the existing success/failure reward, or
`--reward-mode weighted` to compute weighted QoS reward from `traffic.csv`
using `reward/qos_reward.py`.

### Run traffic from a third terminal
```bash
TRAFFIC_DURATION=30 TRAFFIC_EPISODES=1 TRAFFIC_OUTPUT=results/traffic.csv scripts/run_traffic.sh
```

If multiple topology processes are running, pass a PID:
```bash
scripts/run_traffic.sh --pid <pid>
```

Traffic options are controlled via `TRAFFIC_*` environment variables. See
`traffic/README.md` for the full list.
Training traffic runs one measured flow at a time by default for cleaner reward
attribution; set `TRAFFIC_CONCURRENT=1` when you intentionally want overlapping
load-test flows.

### Run a full experiment
```bash
python3 experiments/run_experiment.py --controllers dijkstra,rlearner,roptimizer,staged_controller --topologies abilene --duration 10 --episodes 2 --pair-count 2 --protocols both
```

For a quick smoke test:
```bash
python3 experiments/run_experiment.py --controllers dijkstra --topologies abilene --duration 5 --episodes 1 --pair-count 1 --protocols tcp --fail-fast
```

Each run writes logs and traffic CSV files under `results/experiments/<timestamp>/`.
The comparison table is written to `summary.csv` in that directory.

## System Dependencies
These are installed outside of `pip`:
- Mininet
- Open vSwitch
- iPerf (or iPerf3)

## Notes
- The topologies are designed for research and experimentation, not for production use.
- TCI (Topological Complexity Index) is calculated dynamically to reflect network redundancy.

---

*This README was generated with the help of AI. For questions or contributions, please open an issue or pull request.*

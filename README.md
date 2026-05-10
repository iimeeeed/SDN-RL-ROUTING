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
```

### Run the topology
```bash
scripts/run_topology.sh abilene
scripts/run_topology.sh fat_tree
scripts/run_topology.sh abilene_imp
```

`AUTO_TRAFFIC` defaults to `0` in the script. Set `AUTO_TRAFFIC=1` to run
traffic automatically from within the topology process.

### Run traffic from a third terminal
```bash
TRAFFIC_DURATION=30 scripts/run_traffic.sh
```

If multiple topology processes are running, pass a PID:
```bash
scripts/run_traffic.sh --pid <pid>
```

Traffic options are controlled via `TRAFFIC_*` environment variables. See
`traffic/README.md` for the full list.

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

# SDN RL Routing Project

This project provides a platform for experimenting with Software Defined Networking (SDN) routing algorithms, including Dijkstra and reinforcement learning-based approaches, using the Ryu controller and Mininet network emulator.

## Features
- **Custom Topologies:** Includes Abilene and Fat Tree topologies with adjustable complexity and redundancy.
- **Dijkstra Controller:** Implements shortest-path routing using the Dijkstra algorithm.
- **Reinforcement Learning Controller:** (Optional) For advanced routing experiments.
- **Validation Scripts:** Ensures topologies and port maps are consistent and correct.
- **Performance Testing:** Use iPerf3 to measure network throughput and latency.

## Requirements
- Python 3.9.19
- Ryu 4.34
- Eventlet 0.30.2
- Mininet 2.3.0
- Open vSwitch 3.3.4
- iPerf3 3.16

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
4. **Run the Dijkstra controller:**
   ```bash
   TOPOLOGY=abilene ryu-manager controllers/dijkstra.py
   # or
   TOPOLOGY=fat_tree ryu-manager controllers/dijkstra.py
   ```
5. **Test connectivity and performance:**
   Use Mininet CLI commands like `pingall` and `iperf`.

## Project Structure
- `controllers/` — SDN controller implementations
- `topologies/` — Mininet topology scripts
- `topology_data/` — Data files for topologies (used by controllers)
- `scripts/` — Validation and utility scripts
- `report/`, `results/` — For experiment outputs and analysis

## Notes
- The topologies are designed for research and experimentation, not for production use.
- TCI (Topological Complexity Index) is calculated dynamically to reflect network redundancy.

---

*This README was generated with the help of AI. For questions or contributions, please open an issue or pull request.*

# Member 4 — Baselines and Evaluation (implementation record)

This note records what was implemented on branch **`member4/baselines-evaluation`** relative to the course project plan (QoS-aware dual-agent RL for SDN routing). It is for teammates and grading clarity.

## Planned responsibilities (project plan summary)

- Implement **ECMP** in Ryu alongside existing baselines.
- Design **`experiments/run_experiment.py`** so one invocation (with config) selects topology, agent, reward/exploration (when available), and writes **uniquely named CSV** outputs.
- **Execute** the full experimental grid: nine conditions × two topologies × 100 episodes; collect four QoS metrics plus learning-dynamics fields.
- Maintain a **results ledger** and run **sanity checks** (e.g. flag/re-run episodes more than 3σ from the running mean on a schedule).

## Implemented in this branch

### 1. ECMP baseline — `controllers/ecmp.py`

- Same **topology loading** as `controllers/dijkstra.py` (`topology_data`, **NetworkX** weighted graph).
- **Equal-cost next hops**: at each switch, all neighbours that preserve shortest-path distance to the destination host’s attachment switch.
- **OpenFlow 1.3** reactive routing: table-miss / controller forwarding, then installs flows.
- **Multi-path forwarding**: **SELECT group** when multiple equal-cost output ports exist; single **OUTPUT** when only one next hop exists.
- **Triggers**: ARP and known host-to-host Ethernet unicast (mirrors Dijkstra controller behaviour).

### 2. Experiment runner — `experiments/run_experiment.py` + `experiments/config.yaml`

- Loads YAML (`condition`, `topology`, `controller`, `episodes`, `episode_duration_sec`, traffic pair, UDP bitrate, Ryu wait).
- Starts **`ryu-manager`** for **`dijkstra`** or **`ecmp`** with **`TOPOLOGY`** set.
- Starts Mininet via **`topologies.<topology>.create_network()`** (no interactive CLI in automated runs).
- Per episode: **TCP iPerf3** (throughput), **UDP iPerf3** (jitter / PLR), **ping** (RTT).
- Appends rows to a **timestamped CSV** under **`results/`** (CSV files themselves remain gitignored by `.gitignore`; see below).

### 3. Mininet hooks — `topologies/fat_tree.py`, `topologies/abilene.py`

- **`create_network()`**: build and **start** the network; caller runs **`net.stop()`**.
- **`main()`**: CLI for interactive use; **`if __name__ == "__main__"`** unchanged from a user perspective.

### 4. Results directory — `results/.gitkeep`

- Ensures **`results/`** exists in the repo; generated **`*.csv`** are still ignored.

### 5. Sanity checking (partial)

- **`sanity_flag`** column: throughput compared to prior episodes in the **same run**; **`outlier`** if beyond **3σ** of the history (requires ≥2 prior samples).

### 6. README

- ECMP usage, experiment command, dry-run, branch pointer.

## Not implemented yet (follow-up / integration)

- **Full nine-condition grid** in one driver (BIN-FIX, QOS-EXP, … + RL + R-OPT-ORIG): needs **`rlearner.py` / `roptimizer.py`** and reward/exploration wiring when Members 1–3 finalize interfaces.
- **Separate results ledger** spreadsheet beyond CSV rows (condition × topology × episode tracking file).
- **Sanity protocol** exactly as in the plan (e.g. check every **20** episodes, **automatic re-run** of flagged episodes).
- **Actual execution** of 100 episodes × all conditions (runtime on the Mininet host, not committed output).

## How to run (Ubuntu / Mininet host)

```bash
git checkout member4/baselines-evaluation
python3 experiments/run_experiment.py --config experiments/config.yaml --dry-run
# Preserve venv on PATH when using sudo:
sudo env PATH="$PATH" "$(which python3)" experiments/run_experiment.py --config experiments/config.yaml
```

ECMP alone (manual two-terminal workflow):

```bash
TOPOLOGY=fat_tree ryu-manager controllers/ecmp.py
sudo python3 topologies/fat_tree.py   # interactive CLI
```

## OpenFlow note

The project plan mentions **OpenFlow 1.0**; this codebase uses **OpenFlow 1.3** (see topology `protocols` and `ofproto_v1_3` in controllers). ECMP uses features appropriate for 1.3 (e.g. group tables).

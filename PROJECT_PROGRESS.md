# SDN RL Routing Project Progress

Generated: 2026-05-22

This report summarizes the progress visible in the repository: implementation status, experiment infrastructure, validation, result artifacts, and observed outcomes from completed runs.

## 2026-05-27 Update: Fixes Behind the Stable Fat-Tree R-Learner Result

The strongest recent result is:

```text
results/experiments/20260526-212303_fat-tree-rlearner-v16-core-bottleneck-fixedfeedback-300ep/01_rlearner_fat_tree
```

This run was the first one that showed clean, visible R-Learner improvement on
the fat-tree topology. The main improvement was not higher throughput, which
was already near the offered/load-limited ceiling. The clear improvement was
RTT control: high-RTT paths disappeared and the run stabilized around low RTT
paths.

Observed evidence from the run:

- Completed hundreds of clean episodes with `6` rows per episode.
- No traffic `stderr`, controller tracebacks, skipped weighted Q updates, or
  missing pending feedback decisions.
- RTT improved from early unstable/high values to about `15 ms` in the stable
  phase.
- High-RTT flows dropped from roughly `1.8` per episode early to `0` in the
  stable windows.
- Q updates became consistently positive in the stable phase, with recent Q
  positive fraction reaching `100%`.

The fixes and experiment changes that produced this result were:

### 1. Live QoS Feedback Was Made Reliable

Files:

- `traffic/generate_traffic.py`
- `controllers/rlearner.py`
- `experiments/run_experiment.py`

What changed:

- Concurrent traffic now sends all `flow_start` records first, waits for the
  feedback grace period once, then starts all clients together.
- The experiment runner forwards live feedback and UDP offered-load
  environment variables reliably.
- Controller environment values passed through `sudo -E env ...` now override
  config defaults instead of being overwritten by the runner.
- The R-Learner keeps feedback mappings by measured flow key and applies the
  resulting live QoS reward to the pending routing decision.

Why it mattered:

Before this, traffic could run while the controller missed or misaligned the
reward window. That made Q updates sparse, stale, or missing. After the fix,
each measured TCP/UDP flow group produced live QoS feedback and Q updates.

Simple example:

```text
flow_start(h1->h12, episode=20)
controller selects path s5->s8
traffic finishes and reports throughput, RTT, jitter, loss
controller updates Q(state=h1->h12, action=s5->s8)
```

### 2. TCP and UDP Rewards Were Matched as One QoS Group

File:

- `controllers/rlearner.py`

What changed:

- The pending feedback queue no longer rejects a second pending update for the
  same flow group when it represents a distinct routing decision.
- When a complete QoS reward is available, the controller applies it to all
  pending updates attached to that feedback key.

Why it mattered:

The reward uses TCP throughput plus UDP jitter/loss plus ping RTT. If TCP and
UDP decisions in the same flow group are not credited together, learning sees a
partial or misassigned signal. The fix makes the reward attribution consistent.

Simple example:

```text
TCP gives throughput = 64 Mbps
UDP gives jitter/loss = 0.33 ms / 0.12%
ping gives RTT = 15 ms
one combined QoS reward updates the selected path decision
```

### 3. The Reward Was Changed to Absolute QoS Scaling

File:

- `reward/qos_reward.py`

What changed:

- Added absolute normalization controlled by:

```text
QOS_REWARD_NORMALIZATION=absolute
```

- Added thresholds:

```text
QOS_THROUGHPUT_TARGET_GBPS
QOS_RTT_BAD_MS
QOS_JITTER_BAD_MS
QOS_PLR_BAD_PCT
```

Why it mattered:

Rolling min/max normalization made reward unstable because the meaning of
"good" changed during the run. Absolute thresholds made the reward comparable
across episodes.

Simple example:

```text
RTT 15 ms against QOS_RTT_BAD_MS=120 -> small RTT penalty
RTT 250 ms against QOS_RTT_BAD_MS=120 -> full RTT penalty
```

### 4. High RTT Was Explicitly Guarded

File:

- `reward/qos_reward.py`

What changed:

- Added hard RTT guard:

```text
QOS_HARD_RTT_MS
QOS_HARD_RTT_PENALTY
```

Fat-tree v16 used:

```text
QOS_RTT_BAD_MS=120
QOS_HARD_RTT_MS=100
QOS_HARD_RTT_PENALTY=0.7
```

Why it mattered:

Earlier runs sometimes improved throughput while choosing paths with very high
RTT. The hard RTT guard made those paths strongly negative, so the learner
could not treat high-throughput/high-delay paths as acceptable.

Simple example:

```text
RTT = 15 ms  -> no hard penalty
RTT = 250 ms -> hard penalty applies and pushes reward close to -1
```

The active one-line reward formula was:

```text
reward = clip(
  w_throughput * min(throughput_gbps / throughput_target_gbps, 1)
  - w_rtt * min(rtt_ms / rtt_bad_ms, 1)
  - w_jitter * min(jitter_ms / jitter_bad_ms, 1)
  - w_loss * min(loss_pct / loss_bad_pct, 1)
  - hard_rtt_penalty * min(max(rtt_ms - hard_rtt_ms, 0) / hard_rtt_ms, 1),
  -1,
  1
)
```

For fat-tree v16:

```text
w_throughput=0.10
w_rtt=0.50
w_jitter=0.10
w_loss=0.30
throughput_target_gbps=0.045
rtt_bad_ms=120
jitter_bad_ms=8
loss_bad_pct=1.0
hard_rtt_ms=100
hard_rtt_penalty=0.7
```

### 5. The Reward Profile Was Made RTT/Loss Strict

File:

- `reward/qos_reward.py`

What changed:

- Added and used:

```text
WEIGHTED_PROFILE=rtt_loss_guard
```

With weights:

```text
throughput = 0.10
RTT        = 0.50
jitter     = 0.10
loss       = 0.30
```

Why it mattered:

This made the controller optimize the QoS behavior we actually wanted: avoid
high RTT and packet loss first, then preserve throughput.

Simple example:

```text
Two paths both give 64 Mbps.
Path A has 15 ms RTT.
Path B has 250 ms RTT.
The RTT-heavy reward strongly prefers Path A.
```

### 6. Advantage Reward and Pair Baselines Were Added

File:

- `controllers/rlearner.py`

What changed:

- Added pair-level reward baselines:

```text
REWARD_BASELINE_SCOPE=pair
REWARD_BASELINE_ALPHA=0.06
```

- Added scaled advantage targets:

```text
USE_ADVANTAGE_REWARD=1
ADVANTAGE_REWARD_SCALE=3.0
```

- Added direct negative reward handling:

```text
BAD_REWARD_DIRECT_THRESHOLD=-0.10
```

Why it mattered:

Pair-level baselines let the learner compare a path against recent behavior for
the same source/destination pair. Very bad rewards bypass the advantage
baseline and directly punish the action.

Simple example:

```text
Recent h1->h12 baseline reward = -0.20
New path reward = -0.02
Advantage = +0.18
The path is learned as better even if absolute reward is still near zero.
```

### 7. Candidate Paths and Exploration Were Controlled

File:

- `controllers/rlearner.py`

What changed:

- Bounded the path search and candidate set:

```text
PATH_CUTOFF=5
MAX_CANDIDATE_PATHS=4
MAX_PATH_WEIGHT_DELTA=1
FILTER_TRANSIT_HOST_SWITCHES=1
```

- Reduced excessive exploration while keeping UCB:

```text
RLEARNER_SELECTION=ucb
UCB_C=0.015
EPSILON_TYPE=linear
EPSILON=0.04
EPSILON_MIN=0.002
EPSILON_BETA=0.0015
WARMUP_TRIALS_PER_ACTION=1
```

Why it mattered:

Earlier runs sampled too many similarly bad paths for too long. The fixed setup
still explores, but it stops repeatedly revisiting bad high-RTT choices once
the reward makes them clearly negative.

Simple example:

```text
Instead of trying dozens of long/simple paths, the learner evaluates a small
near-shortest candidate set and quickly suppresses high-delay choices.
```

### 8. Congestion-Aware State and Path Penalties Were Added

File:

- `controllers/rlearner.py`

What changed:

- Added congestion-aware state:

```text
USE_CONGESTION_STATE=1
PORT_STATS_INTERVAL=1
LINK_CAPACITY_MBPS=80
```

- Added runtime path penalties:

```text
PATH_UTILIZATION_PENALTY=0.18
PATH_OVERLAP_PENALTY=0.03
```

Why it mattered:

The same source/destination pair can need different paths under different load.
Congestion state and utilization/overlap penalties give the learner a way to
avoid piling concurrent flows onto the same busy path.

Simple example:

```text
h1->h12 and h2->h11 both want a similar path.
If that path is already active, the overlap penalty makes another candidate
more attractive.
```

### 9. The Experiment Was Made Routing-Limited Instead of Host-Limited

Runtime command change:

```text
TOPO_HOST_BW_MBPS=1000
TOPO_SWITCH_BW_MBPS=80
TRAFFIC_UDP_BW_MBPS=12
```

Why it mattered:

Earlier runs could be dominated by host-edge bottlenecks. In that setup, the
learner cannot improve much by changing routes. The successful fat-tree run
made switch/core links the meaningful bottleneck, so routing decisions affected
RTT and congestion.

Simple example:

```text
If the host link is the bottleneck, every route looks similar.
If switch links are the bottleneck, choosing a less congested path lowers RTT.
```

### 10. Plotting Was Updated to Diagnose Learning

File:

- `analysis/plot_experiment_run.py`

What changed:

- Added parsing for Q updates and path selections.
- Added action learning diagnostics.
- Generated curve-only, rolling-mean plots.
- Preserved controller-reported live reward when available.

Why it mattered:

The important learning signal was not obvious from raw scatter-like plots.
Smoothed curves and Q diagnostics showed that RTT stabilized and Q values
became positive.

Simple example:

```text
Raw rewards hover near 0 because the reward is strict.
Q diagnostics still show the learner prefers low-RTT actions.
```

### Summary of Why the Fat-Tree v16 Run Worked

The good result came from the combination of:

1. Clean live feedback timing.
2. Correct reward-to-action attribution.
3. Absolute QoS reward thresholds.
4. Strong high-RTT punishment.
5. Pair-level advantage learning.
6. Controlled exploration and candidate paths.
7. Congestion-aware state and path penalties.
8. A topology/load setup where routing choices actually affect RTT.

The result should be described as strong and stable, not perfect. The run shows
visible learning through RTT reduction and high-RTT path elimination. Throughput
mostly stays near its ceiling, and reward remains near zero because the reward
function is intentionally strict: good paths receive small positive or
near-zero reward, while bad high-RTT paths receive large negative reward.

## Executive Summary

The project has progressed from basic SDN routing bring-up to a working experiment platform for comparing topology-aware routing controllers under Mininet/Ryu. The repository now includes:

- Three supported topologies: `abilene`, `fat_tree`, and `abilene_imp`.
- Four controller implementations: Dijkstra baseline, R-Learner, R-Optimizer, and a staged R-Learner-to-R-Optimizer controller.
- Repeatable traffic generation with TCP, UDP, ping RTT, episode batching, deterministic pair selection, and live QoS feedback.
- Binary and weighted QoS reward modes.
- Configurable epsilon schedules for RL exploration.
- Automated experiment orchestration with logs, CSV summaries, completion markers, and post-run plotting.
- Stored experiment outputs showing both early integration failures and later complete weighted QoS runs.

The latest complete experimental evidence is concentrated on `abilene_imp` using weighted QoS reward. The strongest stored result is the 100-episode R-Learner run with QoS RTT clipping, which completed 400/400 expected rows and achieved the highest average weighted reward among the saved complete 1000 Mbps runs.

## Repository Scope

The project is organized around these main areas:

| Area | Files / directories | Progress |
| --- | --- | --- |
| Controllers | `controllers/` | Dijkstra, R-Learner, R-Optimizer, and staged dual-agent controllers implemented. |
| Topologies | `topologies/`, `topology_data/` | Runtime Mininet topologies separated from controller topology metadata. |
| Traffic | `traffic/generate_traffic.py`, `traffic/README.md` | Repeatable iPerf traffic generation with CSV output and live feedback support. |
| Rewards | `reward/` | Binary reward and weighted QoS reward implemented. |
| Exploration | `exploration/` | Fixed, linear, and exponential epsilon schedules implemented. |
| Experiments | `experiments/run_experiment.py`, `experiments/config.yaml` | Matrix runner implemented with controller/topology/traffic lifecycle management. |
| Analysis | `analysis/plot_experiment_run.py`, `results/plots/` | Learning-curve plot generation and saved PNG/PDF outputs. |
| Scripts | `scripts/` | Helper scripts for topology/controller runs, traffic injection, cleanup, version checks, and topology validation. |
| Docs | `README.md`, `docs/SETUP.md`, `traffic/README.md` | Setup and usage notes present. |

Approximate implementation size across tracked project files is 7,257 lines, with the largest components being `controllers/staged_controller.py`, `controllers/rlearner.py`, `traffic/generate_traffic.py`, and `experiments/run_experiment.py`.

## Technical Progress

### 1. Topology Layer

Implemented topologies:

| Topology | Switches | Hosts | Backbone links | Total links | TCI |
| --- | ---: | ---: | ---: | ---: | ---: |
| `abilene` | 7 | 5 | 14 | 19 | 0.5714 |
| `fat_tree` | 8 | 12 | 28 | 40 | 0.7500 |
| `abilene_imp` | 20 | 16 | 32 | 48 | 0.40625 |

Progress made:

- Added Mininet runtime topology scripts for all three topologies.
- Added matching `topology_data` modules used by controllers.
- Added DPID mapping support for improved topologies where switch IDs are not simply `sN`.
- Added calculated Topological Complexity Index values.
- Added validation script that checks runtime topology edges against controller metadata and verifies port-map coverage.

Validation status:

```text
python3 scripts/validate_topologies.py
All topology checks passed.
```

### 2. Controller Layer

Implemented controllers:

| Controller | Status | Notes |
| --- | --- | --- |
| `dijkstra` | Implemented and smoke-tested | Baseline shortest-path OpenFlow controller. |
| `rlearner` | Implemented and used in complete runs | Q-learning controller with path cache, epsilon schedules, weighted feedback support, and QoS RTT clipping. |
| `roptimizer` | Implemented | Policy-gradient style optimizer controller with topology-aware path choices. |
| `staged_controller` | Implemented and used in complete runs | Starts with R-Learner exploration, then switches to R-Optimizer refinement after decision/reward thresholds. |

Notable controller progress:

- Controllers load topology metadata dynamically via `TOPOLOGY`.
- Controllers install learned forwarding rules with configurable `LEARNED_FLOW_IDLE_TIMEOUT`.
- R-Learner and staged controller support binary and weighted reward modes.
- Weighted mode receives live traffic feedback over UDP.
- Weighted feedback matching accounts for host pair, protocol, source port, and destination port.
- Staged controller records controller summaries and supports checkpoint save/load.
- Staged controller requires both exploration count and minimum weighted reward feedback before optimizer transition.
- RL path search is bounded by `PATH_CUTOFF` to control route candidates.

### 3. Reward System

Implemented reward modes:

- `binary`: success/failure reward, currently `+10` or `-10`.
- `weighted`: QoS reward computed from throughput, RTT, jitter, and packet loss.

Weighted QoS reward details:

- Formula rewards high throughput and penalizes high RTT, jitter, and packet loss.
- Metrics are normalized over a rolling window.
- Available profiles:
  - `throughput_prioritised`: `(0.50, 0.20, 0.15, 0.15)`
  - `balanced`: `(0.40, 0.30, 0.15, 0.15)`
  - `delay_prioritised`: `(0.25, 0.40, 0.20, 0.15)`
- Reward is clipped to `[-1, +1]`.
- R-Learner supports `QOS_RTT_CLIP_MS`, defaulting to `5000`, to reduce outlier dominance.

### 4. Exploration Schedules

Implemented epsilon schedules:

- Fixed epsilon, default `0.1`.
- Linear decay: `eps(t) = max(eps_min, eps_0 - beta * t)`.
- Exponential decay: `eps(t) = eps_min + (eps_0 - eps_min) * exp(-lambda * t)`.

Schedules are selected through `EPSILON_TYPE` and environment variables such as `EPSILON`, `EPSILON_MIN`, `EPSILON_BETA`, and `EPSILON_LAMBDA`.

### 5. Traffic Generation

Traffic generation has moved beyond ad hoc Mininet commands into a reusable module:

- Supports TCP and UDP iPerf flows.
- Supports ping RTT capture.
- Supports traffic episodes.
- Supports sequential training traffic for cleaner reward attribution.
- Supports optional concurrent traffic for load tests.
- Supports pair modes including `ends`, `random`, `all`, and `east_west`.
- Writes incremental CSV results.
- Writes `.done` completion markers.
- Sends live QoS feedback records to controllers in weighted mode.

Important output columns include:

- `episode`, `src`, `dst`, `protocol`, `port`, `duration_s`
- `throughput_mbps`
- `jitter_ms`
- `loss_pct`
- `rtt_ms`

### 6. Experiment Automation

The project includes an experiment runner at `experiments/run_experiment.py`.

Progress made:

- Matrix execution over controllers and topologies.
- YAML-based defaults in `experiments/config.yaml`.
- Controller, topology, cleanup, and traffic logs per run.
- Traffic timeout estimation from expected flow count.
- Expected-row calculation.
- Completion marker handling.
- Summary CSV generation.
- Weighted reward aggregation from traffic CSVs.
- Fail-fast and cleanup options.

Current default config targets stabilized weighted runs with:

- `REWARD_MODE=weighted`
- `LEARNED_FLOW_IDLE_TIMEOUT=1`
- `PATH_CUTOFF=6`
- `EXPLORE_DECISIONS=50`
- `Q_GAMMA=0.8`
- `EPSILON_TYPE=fixed`
- `EPSILON=0.1`
- `WEIGHTED_PROFILE=balanced`
- sequential traffic by default

### 7. Analysis and Plotting

Implemented analysis script:

- `analysis/plot_experiment_run.py`

Progress made:

- Builds flow-level weighted QoS metrics from traffic CSVs.
- Builds episode-level metrics.
- Parses controller reward logs.
- Writes learning-curve plots as PNG and PDF.

Saved plot artifacts exist for six runs under `results/plots/`, including staged-controller and R-Learner weighted learning curves.

## Results Progress

The repository contains 36 experiment summary files with 48 recorded run rows.

Status distribution:

| Status | Count |
| --- | ---: |
| `failed` | 26 |
| `ok` | 16 |
| `timeout` | 1 |
| `complete` | 5 |

Controller coverage in summaries:

| Controller | Run rows |
| --- | ---: |
| `staged_controller` | 29 |
| `dijkstra` | 8 |
| `rlearner` | 7 |
| `roptimizer` | 4 |

Topology coverage in summaries:

| Topology | Run rows |
| --- | ---: |
| `abilene` | 30 |
| `abilene_imp` | 18 |

### Experiment Timeline

Early phase:

- Initial runs exposed environment and orchestration issues:
  - permission denied on `scripts/run_controller.sh`
  - controller early exits
  - topology early exits
  - missing traffic output
  - incomplete traffic output
- A Dijkstra smoke run completed successfully on `abilene` with 1 TCP row and about 94.5 Mbps throughput.

Middle phase:

- Staged controller completed repeated `abilene` weighted runs with 20 to 80 rows.
- Weighted QoS aggregation appeared in summaries.
- `abilene_imp` was introduced and exercised with staged controller.
- RTT capture was added in later runs.

Later phase:

- Experiments shifted to named, higher-capacity `abilene_imp` runs.
- Complete weighted runs were achieved with expected-row matching.
- Learning-curve plots were generated for complete runs.
- R-Learner 100-episode run with QoS RTT clipping produced the best stored average weighted reward.

### Notable Complete Runs

| Experiment | Controller | Rows | Expected | Avg throughput Mbps | Avg jitter ms | Avg loss % | Avg RTT ms | Weighted rewards | Avg weighted reward |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `20260515-225229_abilene-imp-staged-10ep-weighted-ping` | staged | 40 | 40 | 95.05 | 1.8299 | 0.0000 | 144.4096 | 20 | 0.0213 |
| `20260516-100015_abilene-imp-staged-50ep-weighted-linear-1000mbps-ping` | staged | 200 | 200 | 945.61 | 35.9282 | 0.6864 | 1332.7720 | 100 | -0.0263 |
| `20260516-164947_abilene-imp-staged-50ep-weighted-linear-1000mbps-ping-opt-eps-010` | staged | 200 | 200 | 938.90 | 37.1505 | 3.6644 | 2726.8818 | 99 | 0.0581 |
| `20260516-184056_abilene-imp-rlearner-50ep-weighted-linear-1000mbps-ping` | R-Learner | 200 | 200 | 942.01 | 37.7681 | 1.0110 | 3120.0499 | 100 | -0.0177 |
| `20260516-200435_abilene-imp-rlearner-100ep-weighted-linear-1000mbps-ping-qosclip` | R-Learner | 400 | 400 | 945.625 | 31.9853 | 0.5161 | 1092.8096 | 200 | 0.0918 |

### Result Interpretation

Main result progress:

- The system can now complete full weighted QoS experiments with expected-row matching.
- The staged controller works end to end on `abilene_imp` for both 100 Mbps and 1000 Mbps style runs.
- R-Learner works end to end on `abilene_imp` for 50-episode and 100-episode 1000 Mbps runs.
- RTT clipping in the 100-episode R-Learner run correlates with a clear improvement in average weighted reward and lower average RTT compared with the previous 50-episode R-Learner run.
- The best saved complete weighted result is:
  - `20260516-200435_abilene-imp-rlearner-100ep-weighted-linear-1000mbps-ping-qosclip`
  - 400/400 rows complete
  - 945.625 Mbps average throughput
  - 31.985 ms average jitter
  - 0.516% average loss
  - 1092.810 ms average RTT
  - 0.0918 average weighted reward

Staged-controller observations:

- The 10-episode weighted staged run at lower bandwidth completed cleanly and had positive weighted reward.
- The 50-episode 1000 Mbps staged run without the optimized epsilon variant had slightly negative weighted reward.
- The optimized-epsilon staged run improved average weighted reward to positive territory but had higher packet loss and RTT.

R-Learner observations:

- The first 50-episode 1000 Mbps R-Learner run completed but had negative average weighted reward.
- The later 100-episode QoS-clipped R-Learner run completed with the best weighted reward and better RTT/loss profile.
- A later planned 200-episode R-Learner run did not start because Mininet cleanup required passwordless or cached sudo.

## Generated Artifacts

Experiment outputs:

- `results/experiments/` contains per-run logs, `traffic.csv` files, completion markers, and `summary.csv` files.

Plot outputs:

- `results/plots/20260511-145637_01_staged_controller_abilene_imp_training_aligned_learning_curves.{png,pdf}`
- `results/plots/20260515-225229_abilene-imp-staged-10ep-weighted-ping_01_staged_controller_abilene_imp_weighted_learning_curves.{png,pdf}`
- `results/plots/20260516-100015_abilene-imp-staged-50ep-weighted-linear-1000mbps-ping_01_staged_controller_abilene_imp_weighted_learning_curves.{png,pdf}`
- `results/plots/20260516-164947_abilene-imp-staged-50ep-weighted-linear-1000mbps-ping-opt-eps-010_01_staged_controller_abilene_imp_weighted_learning_curves.{png,pdf}`
- `results/plots/20260516-184056_abilene-imp-rlearner-50ep-weighted-linear-1000mbps-ping_01_rlearner_abilene_imp_weighted_learning_curves.{png,pdf}`
- `results/plots/20260516-200435_abilene-imp-rlearner-100ep-weighted-linear-1000mbps-ping-qosclip_01_rlearner_abilene_imp_weighted_learning_curves.{png,pdf}`

## Environment Progress

Documented system environment in `docs/SETUP.md`:

- pyenv Python 3.9.19
- project-local `.venv`
- Ryu 4.34
- eventlet 0.30.2 compatibility fix
- Mininet 2.3.0
- Open vSwitch 3.3.4
- iPerf3 3.16
- successful built-in single-switch validation

Python dependencies are pinned in `requirements.txt`, including Ryu, Mininet, NetworkX, NumPy, pandas, SciPy, matplotlib, PyYAML, and tqdm.

## Current Gaps and Risks

- Several early and latest runs failed because of environment/orchestration issues, especially sudo cleanup requirements.
- `roptimizer` is implemented but there are no complete saved result rows for it in the current summaries.
- Most final result evidence is on `abilene_imp`; `fat_tree` has topology support and validation, but no complete result summary row was found.
- There are no test files currently visible under `tests/`.
- Some summaries use older schemas while later summaries use newer fields such as `expected_rows`, `weighted_reward_count`, and `avg_weighted_reward`.
- Some runs are marked `ok` while newer complete runs use `complete`, so comparisons should normalize status naming.
- The README still undersells the current RL and weighted-reward implementation compared with the code.

## Recommended Next Steps

1. Re-run a clean comparison matrix with cached sudo or `--skip-cleanup` as appropriate:
   - Dijkstra
   - R-Learner
   - R-Optimizer
   - staged controller
   - at least `abilene` and `abilene_imp`
2. Add complete `fat_tree` experiment results.
3. Add a reproducible benchmark table using the latest summary schema only.
4. Add unit tests for:
   - QoS reward normalization and clipping
   - epsilon schedules
   - topology validation helpers
   - traffic output parsing
5. Update `README.md` to document:
   - weighted reward mode
   - live feedback
   - staged controller behavior
   - experiment runner outputs
   - plotting workflow
6. Preserve the best current result as a baseline:
   - `20260516-200435_abilene-imp-rlearner-100ep-weighted-linear-1000mbps-ping-qosclip`

## Bottom Line

The project has reached a functional research-prototype stage. The core SDN/RL routing stack is implemented, topology validation passes, traffic and experiment automation are in place, and multiple complete weighted QoS experiments have been recorded. The next major progress point is producing a clean, controlled comparison across all controllers and topologies with the latest experiment schema.

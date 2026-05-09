"""
reward/qos_reward.py

Replaces the binary ±10 reward signal from Goteti & Reddy (2025) with a
normalised, weighted combination of four live QoS metrics:

    rt = w1·TP_g  −  w2·RTT_g  −  w3·J_e  −  w4·PLR_g

where each metric is min-max normalised over a rolling window of the last
`window` episodes so the final reward stays in [−1, +1].

Usage
-----
from reward.qos_reward import QoSReward

reward_fn = QoSReward(
    weights=(0.4, 0.3, 0.15, 0.15),   # (w1, w2, w3, w4)
    window=20
)

episode_log = {
    "throughput_gbps": 2.3,
    "rtt_ms":          12.4,
    "jitter_ms":        0.8,
    "plr_pct":          0.5,
}

r = reward_fn.compute_reward(episode_log)   # float in [-1, +1]
history = reward_fn.get_history()           # list of past reward scalars
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
EpisodeLog = Dict[str, float]


# ---------------------------------------------------------------------------
# Weight configurations 
# ---------------------------------------------------------------------------
WEIGHT_CONFIGS: Dict[str, Tuple[float, float, float, float]] = {
    # (w1_throughput, w2_rtt, w3_jitter, w4_plr)
    "throughput_prioritised": (0.50, 0.20, 0.15, 0.15),
    "balanced":               (0.40, 0.30, 0.15, 0.15),   # default
    "delay_prioritised":      (0.25, 0.40, 0.20, 0.15),
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _safe_minmax(value: float, minimum: float, maximum: float) -> float:
    """
    Min-max normalise `value` to [0, 1].

    Edge case: if min == max (no variance yet in the window), return 0.5 so
    the normalised value is neutral rather than 0 or 1.
    """
    if maximum == minimum:
        return 0.5
    return (value - minimum) / (maximum - minimum)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class QoSReward:
    """
    QoS-weighted reward function with rolling min-max normalisation.

    Parameters
    ----------
    weights : tuple of 4 floats  (w1, w2, w3, w4)
        Weights for throughput, RTT, jitter, and PLR.
        Must sum to 1.0 (checked on construction).
    window : int
        Number of recent episodes used to compute running min/max for
        normalisation (default 20, as specified in the project plan).
    """

    REQUIRED_KEYS = ("throughput_gbps", "rtt_ms", "jitter_ms", "plr_pct")

    def __init__(
        self,
        weights: Tuple[float, float, float, float] = WEIGHT_CONFIGS["balanced"],
        window: int = 20,
    ) -> None:
        self._validate_weights(weights)
        self.w1, self.w2, self.w3, self.w4 = weights
        self.window = window

        # Rolling buffers — one deque per raw metric
        self._tp_buf:  deque[float] = deque(maxlen=window)
        self._rtt_buf: deque[float] = deque(maxlen=window)
        self._jit_buf: deque[float] = deque(maxlen=window)
        self._plr_buf: deque[float] = deque(maxlen=window)

        # History of computed reward scalars (useful for convergence plots)
        self._reward_history: List[float] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_reward(self, episode_log: EpisodeLog) -> float:
        """
        Compute the QoS-weighted reward for one episode.

        Steps
        -----
        1. Validate the log dict.
        2. Append raw metrics to the rolling buffers.
        3. Normalise each metric with current window min/max.
        4. Compute the signed weighted sum.
        5. Clip to [-1, +1] as a safety guard.
        6. Append to history and return.

        Parameters
        ----------
        episode_log : dict
            Must contain keys: throughput_gbps, rtt_ms, jitter_ms, plr_pct.
            Zero-throughput and other degenerate values are handled gracefully.

        Returns
        -------
        float
            Reward scalar in [-1, +1].
        """
        self._validate_log(episode_log)

        tp  = episode_log["throughput_gbps"]
        rtt = episode_log["rtt_ms"]
        jit = episode_log["jitter_ms"]
        plr = episode_log["plr_pct"]

        # --- Degenerate input guard ---
        # Zero throughput means the path failed entirely; return minimum reward.
        if tp <= 0.0:
            reward = -1.0
            self._reward_history.append(reward)
            return reward

        # --- Update rolling buffers with current episode values ---
        self._tp_buf.append(tp)
        self._rtt_buf.append(rtt)
        self._jit_buf.append(jit)
        self._plr_buf.append(plr)

        # --- Compute window min/max for each metric ---
        tp_min,  tp_max  = min(self._tp_buf),  max(self._tp_buf)
        rtt_min, rtt_max = min(self._rtt_buf), max(self._rtt_buf)
        jit_min, jit_max = min(self._jit_buf), max(self._jit_buf)
        plr_min, plr_max = min(self._plr_buf), max(self._plr_buf)

        # --- Normalise to [0, 1] ---
        tp_n  = _safe_minmax(tp,  tp_min,  tp_max)
        rtt_n = _safe_minmax(rtt, rtt_min, rtt_max)
        jit_n = _safe_minmax(jit, jit_min, jit_max)
        plr_n = _safe_minmax(plr, plr_min, plr_max)

        # --- Weighted combination ---
        # Throughput: higher is better  → positive contribution
        # RTT, jitter, PLR: lower is better → negative contribution
        reward = (
            + self.w1 * tp_n
            - self.w2 * rtt_n
            - self.w3 * jit_n
            - self.w4 * plr_n
        )

        # Clip to [-1, +1] as a safety guard (should be satisfied by
        # construction since weights sum to 1 and each term is in [0,1])
        reward = max(-1.0, min(1.0, reward))

        self._reward_history.append(reward)
        return reward

    def get_history(self) -> List[float]:
        """Return a copy of all reward scalars computed so far."""
        return list(self._reward_history)

    def reset_history(self) -> None:
        """
        Clear reward history AND rolling normalisation buffers.

        Call this between independent experimental runs to avoid cross-
        contamination of the rolling window.
        """
        self._tp_buf.clear()
        self._rtt_buf.clear()
        self._jit_buf.clear()
        self._plr_buf.clear()
        self._reward_history.clear()

    @property
    def weights(self) -> Tuple[float, float, float, float]:
        return (self.w1, self.w2, self.w3, self.w4)

    @property
    def episode_count(self) -> int:
        return len(self._reward_history)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_weights(weights: Tuple[float, float, float, float]) -> None:
        if len(weights) != 4:
            raise ValueError(f"Expected 4 weights, got {len(weights)}.")
        if any(w < 0 for w in weights):
            raise ValueError("All weights must be non-negative.")
        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Weights must sum to 1.0, got {total:.6f}.")

    def _validate_log(self, log: EpisodeLog) -> None:
        missing = [k for k in self.REQUIRED_KEYS if k not in log]
        if missing:
            raise KeyError(f"episode_log is missing keys: {missing}")
        for k in self.REQUIRED_KEYS:
            if not isinstance(log[k], (int, float)):
                raise TypeError(f"episode_log['{k}'] must be numeric.")
            if log[k] < 0:
                raise ValueError(f"episode_log['{k}'] must be >= 0, got {log[k]}.")
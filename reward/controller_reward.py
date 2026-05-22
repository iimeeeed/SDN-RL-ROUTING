from typing import Optional, Sequence

from reward.binary_reward import compute_reward as compute_binary_reward


SUPPORTED_REWARD_MODES = ("binary", "weighted")


def compute_controller_reward(
    success: bool,
    path: Optional[Sequence[str]] = None,
    mode: str = "binary",
) -> float:
    mode = (mode or "binary").lower()

    if mode == "binary":
        return float(compute_binary_reward(success))

    if mode == "weighted":
        # Live QoS metrics are produced by traffic generation after controller
        # decisions. Controllers keep binary online feedback; run_experiment.py
        # computes weighted QoS reward from traffic.csv for evaluation.
        return float(compute_binary_reward(success))

    raise ValueError(
        f"Unsupported reward mode: {mode}. "
        f"Expected one of: {', '.join(SUPPORTED_REWARD_MODES)}"
    )

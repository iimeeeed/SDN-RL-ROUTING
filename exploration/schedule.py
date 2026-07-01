import os

from exploration.exp_decay import ExponentialDecay
from exploration.fixed_epsilon import FIXED_EPSILON
from exploration.linear_decay import LinearDecay


class FixedEpsilon:
    def __init__(self, epsilon: float = FIXED_EPSILON):
        if epsilon < 0.0 or epsilon > 1.0:
            raise ValueError(f"epsilon must be between 0.0 and 1.0, got {epsilon}")
        self.epsilon = epsilon

    def get_epsilon(self, episode: int = 0) -> float:
        return self.epsilon

    def __repr__(self):
        return f"FixedEpsilon(epsilon={self.epsilon})"


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


def build_epsilon_schedule():
    schedule_type = os.environ.get("EPSILON_TYPE", "fixed").lower()

    if schedule_type == "fixed":
        return FixedEpsilon(_float_env("EPSILON", FIXED_EPSILON))

    if schedule_type == "linear":
        return LinearDecay(
            eps_0=_float_env("EPSILON", 0.4),
            eps_min=_float_env("EPSILON_MIN", 0.05),
            beta=_float_env("EPSILON_BETA", 0.0035),
        )

    if schedule_type in ("exp", "exponential"):
        return ExponentialDecay(
            eps_0=_float_env("EPSILON", 0.5),
            eps_min=_float_env("EPSILON_MIN", 0.05),
            lambda_=_float_env("EPSILON_LAMBDA", 0.05),
        )

    raise ValueError(
        "EPSILON_TYPE must be one of: fixed, linear, exponential"
    )

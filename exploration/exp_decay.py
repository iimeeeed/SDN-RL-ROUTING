"""
Exponential Epsilon-Decay Schedule

Formula:
    eps(t) = eps_min + (eps_0 - eps_min) * exp(-lambda_ * t)

Default hyperparameters (tuned so eps ≈ 0.05 by episode 80):
    eps_0    = 0.5    (starting exploration rate)
    eps_min  = 0.05   (floor — never stop exploring completely)
    lambda_  = 0.05   (decay speed; increase to decay faster)

Usage:
    from exp_decay import ExponentialDecay
    schedule = ExponentialDecay()
    eps = schedule.get_epsilon(episode)   # call once per episode
"""

import math


class ExponentialDecay:
    def __init__(self, eps_0: float = 0.5, eps_min: float = 0.05, lambda_: float = 0.05):
        """
        Parameters
        ----------
        eps_0   : Initial epsilon (exploration rate at episode 0).
        eps_min : Minimum epsilon (floor value, never goes below this).
        lambda_ : Decay constant. Higher = faster decay.
                  Default 0.05 reaches eps_min ≈ 0.05 by episode ~80.
        """
        if not (0 < eps_min < eps_0 <= 1.0):
            raise ValueError("Must satisfy: 0 < eps_min < eps_0 <= 1.0")
        if lambda_ <= 0:
            raise ValueError("lambda_ must be positive")

        self.eps_0 = eps_0
        self.eps_min = eps_min
        self.lambda_ = lambda_

    def get_epsilon(self, episode: int) -> float:
        """
        Return the epsilon value for the given episode number.

        Parameters
        ----------
        episode : Current episode index (0-based).

        Returns
        -------
        float : Epsilon in [eps_min, eps_0].
        """
        if episode < 0:
            raise ValueError("Episode number must be >= 0")
        eps = self.eps_min + (self.eps_0 - self.eps_min) * math.exp(-self.lambda_ * episode)
        return round(eps, 4)

    def __repr__(self):
        return (f"ExponentialDecay(eps_0={self.eps_0}, eps_min={self.eps_min}, "
                f"lambda_={self.lambda_})")

"""
Linear Epsilon-Decay Schedule

Formula:
    eps(t) = max(eps_min, eps_0 - beta * t)

Default hyperparameters (tuned so eps ≈ 0.05 by episode 80):
    eps_0   = 0.4     (starting exploration rate)
    eps_min = 0.05    (floor — never stop exploring completely)
    beta    = 0.0035  (amount subtracted per episode)

Usage:
    from linear_decay import LinearDecay
    schedule = LinearDecay()
    eps = schedule.get_epsilon(episode)   # call once per episode
"""


class LinearDecay:
    def __init__(self, eps_0: float = 0.4, eps_min: float = 0.05, beta: float = 0.0035):
        """
        Parameters
        ----------
        eps_0   : Initial epsilon (exploration rate at episode 0).
        eps_min : Minimum epsilon (floor value, never goes below this).
        beta    : Amount to subtract from epsilon each episode.
                  Default 0.0035 reaches eps_min ≈ 0.05 by episode ~80.
        """
        if not (0 < eps_min < eps_0 <= 1.0):
            raise ValueError("Must satisfy: 0 < eps_min < eps_0 <= 1.0")
        if beta <= 0:
            raise ValueError("beta must be positive")

        self.eps_0 = eps_0
        self.eps_min = eps_min
        self.beta = beta

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
        eps = max(self.eps_min, self.eps_0 - self.beta * episode)
        return round(eps, 4)


    def __repr__(self):
        return (f"LinearDecay(eps_0={self.eps_0}, eps_min={self.eps_min}, "
                f"beta={self.beta})")


SUCCESS_REWARD = 10
FAILURE_REWARD = -10


def compute_reward(success: bool) -> int:
    return SUCCESS_REWARD if success else FAILURE_REWARD

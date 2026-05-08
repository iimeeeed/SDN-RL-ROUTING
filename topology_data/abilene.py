"""
Topology data for project Abilene topology.

Must match topologies/abilene.py.

Required:
- Total nodes: 12
- Total links: 15
- TCI: 0.58
"""

NAME = "abilene"

SWITCHES = [f"s{i}" for i in range(1, 8)]

BACKBONE_LINKS = [
    ("s1", "s2", 1),
    ("s1", "s3", 1),
    ("s1", "s4", 1),
    ("s2", "s3", 1),
    ("s2", "s4", 1),
    ("s2", "s5", 1),
    ("s3", "s5", 1),
    ("s3", "s6", 1),
    ("s4", "s5", 1),
    ("s4", "s6", 1),
    ("s4", "s7", 1),
    ("s5", "s6", 1),
    ("s5", "s7", 1),
    ("s6", "s7", 1),
]

HOST_TO_SWITCH = {
    "h1": "s1",
    "h2": "s2",
    "h3": "s4",
    "h4": "s6",
    "h5": "s7",
}

HOST_TO_IP = {
    f"h{i}": f"10.0.0.{i}" for i in range(1, 6)
}

HOST_TO_MAC = {
    f"h{i}": f"00:00:00:00:00:{i:02x}" for i in range(1, 6)
}

# Port map must match topologies/abilene.py link creation order.
#
# Link order:
# 1. host_links
# 2. backbone_links
PORT_MAP = {
    "s1": {
        "h1": 1,
        "s2": 2,
        "s3": 3,
        "s4": 4,
    },
    "s2": {
        "h2": 1,
        "s1": 2,
        "s3": 3,
        "s4": 4,
        "s5": 5,
    },
    "s3": {
        "s1": 1,
        "s2": 2,
        "s5": 3,
        "s6": 4,
    },
    "s4": {
        "h3": 1,
        "s1": 2,
        "s2": 3,
        "s5": 4,
        "s6": 5,
        "s7": 6,
    },
    "s5": {
        "s2": 1,
        "s3": 2,
        "s4": 3,
        "s6": 4,
        "s7": 5,
    },
    "s6": {
        "h4": 1,
        "s3": 2,
        "s4": 3,
        "s5": 4,
        "s7": 5,
    },
    "s7": {
        "h5": 1,
        "s4": 2,
        "s5": 3,
        "s6": 4,
    },
}

# Topological Complexity Index: cyclomatic complexity of backbone
# = (E - N + 1) / E where E is backbone edges and N is switches
TCI = (len(BACKBONE_LINKS) - len(SWITCHES) + 1) / len(BACKBONE_LINKS)

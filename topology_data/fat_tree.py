"""
Strict k=4 Fat Tree topology data.

k = 4

Switch counts:
- Core:        (k/2)^2 = 4 switches   -> s1-s4
- Aggregation: k*k/2   = 8 switches   -> s5-s12
- Edge:        k*k/2   = 8 switches   -> s13-s20

Hosts:
- k^3/4 = 16 hosts -> h1-h16

This must match topologies/fat_tree.py exactly.
"""

NAME = "fat_tree"

SWITCHES = [f"s{i}" for i in range(1, 21)]

CORE_SWITCHES = ["s1", "s2", "s3", "s4"]

AGG_SWITCHES = [
    "s5", "s6",
    "s7", "s8",
    "s9", "s10",
    "s11", "s12",
]

EDGE_SWITCHES = [
    "s13", "s14",
    "s15", "s16",
    "s17", "s18",
    "s19", "s20",
]

# Each edge switch has 2 hosts.
HOST_TO_SWITCH = {
    "h1": "s13",
    "h2": "s13",

    "h3": "s14",
    "h4": "s14",

    "h5": "s15",
    "h6": "s15",

    "h7": "s16",
    "h8": "s16",

    "h9": "s17",
    "h10": "s17",

    "h11": "s18",
    "h12": "s18",

    "h13": "s19",
    "h14": "s19",

    "h15": "s20",
    "h16": "s20",
}

HOST_TO_IP = {
    f"h{i}": f"10.0.0.{i}" for i in range(1, 17)
}

HOST_TO_MAC = {
    f"h{i}": f"00:00:00:00:00:{i:02x}" for i in range(1, 17)
}

# Strict k=4 Fat Tree links.
#
# Pods:
# Pod 0: aggregation s5,s6   edge s13,s14
# Pod 1: aggregation s7,s8   edge s15,s16
# Pod 2: aggregation s9,s10  edge s17,s18
# Pod 3: aggregation s11,s12 edge s19,s20
#
# Edge-to-aggregation:
# Every edge switch connects to both aggregation switches in its pod.
#
# Aggregation-to-core:
# In each pod:
# - first aggregation switch connects to core s1,s2
# - second aggregation switch connects to core s3,s4
BACKBONE_LINKS = [
    # Pod 0 edge-aggregation links
    ("s13", "s5", 1), ("s13", "s6", 1),
    ("s14", "s5", 1), ("s14", "s6", 1),

    # Pod 1 edge-aggregation links
    ("s15", "s7", 1), ("s15", "s8", 1),
    ("s16", "s7", 1), ("s16", "s8", 1),

    # Pod 2 edge-aggregation links
    ("s17", "s9", 1), ("s17", "s10", 1),
    ("s18", "s9", 1), ("s18", "s10", 1),

    # Pod 3 edge-aggregation links
    ("s19", "s11", 1), ("s19", "s12", 1),
    ("s20", "s11", 1), ("s20", "s12", 1),

    # Aggregation-core links: pod 0
    ("s5", "s1", 1), ("s5", "s2", 1),
    ("s6", "s3", 1), ("s6", "s4", 1),

    # Aggregation-core links: pod 1
    ("s7", "s1", 1), ("s7", "s2", 1),
    ("s8", "s3", 1), ("s8", "s4", 1),

    # Aggregation-core links: pod 2
    ("s9", "s1", 1), ("s9", "s2", 1),
    ("s10", "s3", 1), ("s10", "s4", 1),

    # Aggregation-core links: pod 3
    ("s11", "s1", 1), ("s11", "s2", 1),
    ("s12", "s3", 1), ("s12", "s4", 1),
]

# Port map must match topologies/fat_tree.py link creation order:
#
# 1. Host links are added first:
#    h1,h2 to s13
#    h3,h4 to s14
#    ...
#    h15,h16 to s20
#
# 2. Switch links are added in BACKBONE_LINKS order above.
PORT_MAP = {
    # Core switches
    "s1": {
        "s5": 1,
        "s7": 2,
        "s9": 3,
        "s11": 4,
    },
    "s2": {
        "s5": 1,
        "s7": 2,
        "s9": 3,
        "s11": 4,
    },
    "s3": {
        "s6": 1,
        "s8": 2,
        "s10": 3,
        "s12": 4,
    },
    "s4": {
        "s6": 1,
        "s8": 2,
        "s10": 3,
        "s12": 4,
    },

    # Aggregation switches
    "s5": {
        "s13": 1,
        "s14": 2,
        "s1": 3,
        "s2": 4,
    },
    "s6": {
        "s13": 1,
        "s14": 2,
        "s3": 3,
        "s4": 4,
    },
    "s7": {
        "s15": 1,
        "s16": 2,
        "s1": 3,
        "s2": 4,
    },
    "s8": {
        "s15": 1,
        "s16": 2,
        "s3": 3,
        "s4": 4,
    },
    "s9": {
        "s17": 1,
        "s18": 2,
        "s1": 3,
        "s2": 4,
    },
    "s10": {
        "s17": 1,
        "s18": 2,
        "s3": 3,
        "s4": 4,
    },
    "s11": {
        "s19": 1,
        "s20": 2,
        "s1": 3,
        "s2": 4,
    },
    "s12": {
        "s19": 1,
        "s20": 2,
        "s3": 3,
        "s4": 4,
    },

    # Edge switches
    "s13": {
        "h1": 1,
        "h2": 2,
        "s5": 3,
        "s6": 4,
    },
    "s14": {
        "h3": 1,
        "h4": 2,
        "s5": 3,
        "s6": 4,
    },
    "s15": {
        "h5": 1,
        "h6": 2,
        "s7": 3,
        "s8": 4,
    },
    "s16": {
        "h7": 1,
        "h8": 2,
        "s7": 3,
        "s8": 4,
    },
    "s17": {
        "h9": 1,
        "h10": 2,
        "s9": 3,
        "s10": 4,
    },
    "s18": {
        "h11": 1,
        "h12": 2,
        "s9": 3,
        "s10": 4,
    },
    "s19": {
        "h13": 1,
        "h14": 2,
        "s11": 3,
        "s12": 4,
    },
    "s20": {
        "h15": 1,
        "h16": 2,
        "s11": 3,
        "s12": 4,
    },
}

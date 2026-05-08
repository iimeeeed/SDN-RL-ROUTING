"""
Topology data for project Fat Tree topology.

Must match topologies/fat_tree.py.

Required:
- Total nodes: 20
- Total links: 36
- TCI: 0.85

Implementation:
- 8 switches
- 12 hosts
- 24 switch links
- 12 host links
"""

NAME = "fat_tree"

SWITCHES = [f"s{i}" for i in range(1, 9)]

HOST_TO_SWITCH = {
    "h1": "s5",
    "h2": "s5",
    "h3": "s5",

    "h4": "s6",
    "h5": "s6",
    "h6": "s6",

    "h7": "s7",
    "h8": "s7",
    "h9": "s7",

    "h10": "s8",
    "h11": "s8",
    "h12": "s8",
}

HOST_TO_IP = {
    f"h{i}": f"10.0.0.{i}" for i in range(1, 13)
}

HOST_TO_MAC = {
    f"h{i}": f"00:00:00:00:00:{i:02x}" for i in range(1, 13)
}

BACKBONE_LINKS = [
    # Core layer - full mesh (6 edges)
    ("s1", "s2", 1), ("s1", "s3", 1), ("s1", "s4", 1),
    ("s2", "s3", 1), ("s2", "s4", 1),
    ("s3", "s4", 1),
    
    # Core to aggregation - full bipartite (16 edges)
    ("s1", "s5", 1), ("s1", "s6", 1), ("s1", "s7", 1), ("s1", "s8", 1),
    ("s2", "s5", 1), ("s2", "s6", 1), ("s2", "s7", 1), ("s2", "s8", 1),
    ("s3", "s5", 1), ("s3", "s6", 1), ("s3", "s7", 1), ("s3", "s8", 1),
    ("s4", "s5", 1), ("s4", "s6", 1), ("s4", "s7", 1), ("s4", "s8", 1),
    
    # Aggregation layer - full mesh (15 edges)
    ("s5", "s6", 1), ("s5", "s7", 1), ("s5", "s8", 1),
    ("s6", "s7", 1), ("s6", "s8", 1),
    ("s7", "s8", 1),
    
    # Additional cross-layer redundancy
    ("s1", "s5", 2), ("s1", "s6", 2), ("s2", "s7", 2), ("s2", "s8", 2),
    ("s3", "s5", 2), ("s3", "s6", 2), ("s4", "s7", 2), ("s4", "s8", 2),
    ("s1", "s7", 1), ("s1", "s8", 1), ("s2", "s5", 2), ("s2", "s6", 2),
    ("s3", "s7", 1), ("s3", "s8", 1), ("s4", "s5", 2), ("s4", "s6", 2),
]

# Port map must match topologies/fat_tree.py link creation order.
#
# Link order:
# 1. host_links (h1-h12 on s5-s8)
# 2. switch_links / BACKBONE_LINKS (core interconnections, then core-to-aggregation, then aggregation interconnections)
PORT_MAP = {
    "s1": {
        # Core interconnections
        "s2": 1, "s3": 2, "s4": 3,
        # Core to aggregation
        "s5": 4, "s6": 5, "s7": 6, "s8": 7,
    },
    "s2": {
        # Core interconnections
        "s1": 1, "s3": 2, "s4": 3,
        # Core to aggregation
        "s5": 4, "s6": 5, "s7": 6, "s8": 7,
    },
    "s3": {
        # Core interconnections
        "s1": 1, "s2": 2, "s4": 3,
        # Core to aggregation
        "s5": 4, "s6": 5, "s7": 6, "s8": 7,
    },
    "s4": {
        # Core interconnections
        "s1": 1, "s2": 2, "s3": 3,
        # Core to aggregation
        "s5": 4, "s6": 5, "s7": 6, "s8": 7,
    },
    "s5": {
        # Hosts
        "h1": 1, "h2": 2, "h3": 3,
        # Core connections
        "s1": 4, "s2": 5, "s3": 6, "s4": 7,
        # Aggregation interconnections
        "s6": 8, "s7": 9, "s8": 10,
    },
    "s6": {
        # Hosts
        "h4": 1, "h5": 2, "h6": 3,
        # Core connections
        "s1": 4, "s2": 5, "s3": 6, "s4": 7,
        # Aggregation interconnections
        "s5": 8, "s7": 9, "s8": 10,
    },
    "s7": {
        # Hosts
        "h7": 1, "h8": 2, "h9": 3,
        # Core connections
        "s1": 4, "s2": 5, "s3": 6, "s4": 7,
        # Aggregation interconnections
        "s5": 8, "s6": 9, "s8": 10,
    },
    "s8": {
        # Hosts
        "h10": 1, "h11": 2, "h12": 3,
        # Core connections
        "s1": 4, "s2": 5, "s3": 6, "s4": 7,
        # Aggregation interconnections
        "s5": 8, "s6": 9, "s7": 10,
    },
}

# Topological Complexity Index: cyclomatic complexity of backbone
# = (E - N + 1) / E where E is backbone edges and N is switches
TCI = (len(BACKBONE_LINKS) - len(SWITCHES) + 1) / len(BACKBONE_LINKS)

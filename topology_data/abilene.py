"""
Topology data for the realistic Abilene/Internet2 backbone.

Must match topologies/abilene.py.
"""

NAME = "abilene"

SWITCHES = [
    "s1", "s2", "s3", "s4", "s5", "s6",
    "s7", "s8", "s9", "s10", "s11"
]

BACKBONE_LINKS = [
    ("s1", "s2", 1295),
    ("s2", "s3", 366),
    ("s2", "s4", 1295),
    ("s3", "s6", 1893),

    ("s4", "s5", 587),
    ("s5", "s6", 902),
    ("s5", "s8", 587),

    ("s1", "s7", 2095),
    ("s7", "s8", 260),
    ("s7", "s11", 639),

    ("s6", "s9", 1176),
    ("s8", "s9", 1176),
    ("s9", "s10", 846),
    ("s10", "s11", 233),
]

HOST_TO_SWITCH = {
    f"h{i}": f"s{i}" for i in range(1, 12)
}

HOST_TO_IP = {
    f"h{i}": f"10.0.0.{i}" for i in range(1, 12)
}

HOST_TO_MAC = {
    f"h{i}": f"00:00:00:00:00:{i:02x}" for i in range(1, 12)
}

# Port map must match the link order in topologies/abilene.py.
# In topologies/abilene.py, host links are added first, then backbone links.
PORT_MAP = {
    "s1": {
        "h1": 1,
        "s2": 2,
        "s7": 3,
    },
    "s2": {
        "h2": 1,
        "s1": 2,
        "s3": 3,
        "s4": 4,
    },
    "s3": {
        "h3": 1,
        "s2": 2,
        "s6": 3,
    },
    "s4": {
        "h4": 1,
        "s2": 2,
        "s5": 3,
    },
    "s5": {
        "h5": 1,
        "s4": 2,
        "s6": 3,
        "s8": 4,
    },
    "s6": {
        "h6": 1,
        "s3": 2,
        "s5": 3,
        "s9": 4,
    },
    "s7": {
        "h7": 1,
        "s1": 2,
        "s8": 3,
        "s11": 4,
    },
    "s8": {
        "h8": 1,
        "s5": 2,
        "s7": 3,
        "s9": 4,
    },
    "s9": {
        "h9": 1,
        "s6": 2,
        "s8": 3,
        "s10": 4,
    },
    "s10": {
        "h10": 1,
        "s9": 2,
        "s11": 3,
    },
    "s11": {
        "h11": 1,
        "s7": 2,
        "s10": 3,
    },
}

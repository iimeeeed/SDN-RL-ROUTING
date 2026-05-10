#!/usr/bin/env python3

import sys
import os
import importlib

# Add project root to path so topology_data can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


EXPECTED = {
    "abilene": {
        "nodes": 12,
        "links": 19,  # 14 backbone + 5 host links
    },
    "fat_tree": {
        "nodes": 20,
        "links": 56,  # 44 backbone + 12 host links
    },
}


def validate(name):
    topo = importlib.import_module(f"topology_data.{name}")

    switches = len(topo.SWITCHES)
    hosts = len(topo.HOST_TO_SWITCH)
    nodes = switches + hosts

    host_links = len(topo.HOST_TO_SWITCH)
    switch_links = len(topo.BACKBONE_LINKS)
    links = host_links + switch_links

    tci = topo.TCI

    expected = EXPECTED[name]

    print(f"\n{name}")
    print("-" * len(name))
    print(f"switches: {switches}")
    print(f"hosts: {hosts}")
    print(f"nodes: {nodes} / expected {expected['nodes']}")
    print(f"host links: {host_links}")
    print(f"switch links: {switch_links}")
    print(f"links: {links} / expected {expected['links']}")
    print(f"TCI: {tci} (calculated)")

    assert nodes == expected["nodes"], f"{name}: wrong node count"
    assert links == expected["links"], f"{name}: wrong link count"

    # Check port maps cover host links.
    for host, sw in topo.HOST_TO_SWITCH.items():
        assert host in topo.PORT_MAP[sw], f"{name}: missing port {sw}->{host}"

    # Check port maps cover backbone links in both directions.
    for u, v, _ in topo.BACKBONE_LINKS:
        assert v in topo.PORT_MAP[u], f"{name}: missing port {u}->{v}"
        assert u in topo.PORT_MAP[v], f"{name}: missing port {v}->{u}"

    print("OK")


def main():
    validate("abilene")
    validate("fat_tree")
    print("\nAll topology checks passed.")


if __name__ == "__main__":
    main()

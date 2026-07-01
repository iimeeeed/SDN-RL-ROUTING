#!/usr/bin/env python3

import ast
import importlib
import os
import sys

# Add project root to path so topology_data can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


TOPOLOGIES = {
    "abilene": "topologies/abilene.py",
    "fat_tree": "topologies/fat_tree.py",
    "abilene_imp": "topologies/abilene_imp.py",
}


def literal_assignments(path):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    values = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            values[node.targets[0].id] = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
    return values


def normalize_edge(left, right):
    return tuple(sorted((left, right)))


def extract_runtime_edges(name):
    path = os.path.join(project_root, TOPOLOGIES[name])
    values = literal_assignments(path)

    if name == "abilene_imp":
        host_links = [
            normalize_edge(host, switch)
            for switch, host in values["edge_host_links"]
        ]
        switch_links = [
            normalize_edge(left, right)
            for key in ("core_agg_links", "agg_edge_links")
            for left, right in values[key]
        ]
    else:
        host_links = [
            normalize_edge(host, switch)
            for host, switch in values["host_links"]
        ]
        switch_key = "backbone_links" if name == "abilene" else "switch_links"
        switch_links = [
            normalize_edge(left, right)
            for left, right in values[switch_key]
        ]

    return set(host_links), set(switch_links)


def validate(name):
    topo = importlib.import_module(f"topology_data.{name}")
    runtime_host_links, runtime_switch_links = extract_runtime_edges(name)

    switches = len(topo.SWITCHES)
    hosts = len(topo.HOST_TO_SWITCH)
    nodes = switches + hosts

    host_links = len(topo.HOST_TO_SWITCH)
    switch_links = len(topo.BACKBONE_LINKS)
    links = host_links + switch_links

    tci = topo.TCI

    data_host_links = {
        normalize_edge(host, switch)
        for host, switch in topo.HOST_TO_SWITCH.items()
    }
    data_switch_links = {
        normalize_edge(u, v)
        for u, v, _weight in topo.BACKBONE_LINKS
    }

    print(f"\n{name}")
    print("-" * len(name))
    print(f"switches: {switches}")
    print(f"hosts: {hosts}")
    print(f"nodes: {nodes}")
    print(f"host links: {host_links} / runtime {len(runtime_host_links)}")
    print(f"switch links: {switch_links} / runtime {len(runtime_switch_links)}")
    print(f"links: {links} / runtime {len(runtime_host_links) + len(runtime_switch_links)}")
    print(f"TCI: {tci} (calculated)")

    assert data_host_links == runtime_host_links, (
        f"{name}: topology_data host links do not match runtime topology "
        f"(missing={runtime_host_links - data_host_links}, "
        f"extra={data_host_links - runtime_host_links})"
    )
    assert data_switch_links == runtime_switch_links, (
        f"{name}: topology_data switch links do not match runtime topology "
        f"(missing={runtime_switch_links - data_switch_links}, "
        f"extra={data_switch_links - runtime_switch_links})"
    )

    # Check port maps cover host links.
    for host, sw in topo.HOST_TO_SWITCH.items():
        assert host in topo.PORT_MAP[sw], f"{name}: missing port {sw}->{host}"

    # Check port maps cover backbone links in both directions.
    for u, v, _ in topo.BACKBONE_LINKS:
        assert v in topo.PORT_MAP[u], f"{name}: missing port {u}->{v}"
        assert u in topo.PORT_MAP[v], f"{name}: missing port {v}->{u}"

    print("OK")


def main():
    for name in TOPOLOGIES:
        validate(name)
    print("\nAll topology checks passed.")


if __name__ == "__main__":
    main()

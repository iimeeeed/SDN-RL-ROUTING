#!/usr/bin/env python3

"""
Fat-Tree k=4 topology.

Structure (matches attached diagram):
  - 4  core switches  : c0–c3          (top layer)
  - 8  agg  switches  : a1–a8          (2 per pod)
  - 8  edge switches  : e1–e8          (2 per pod)
  - 16 hosts          : h1–h16         (2 per edge switch)
  - 4  pods           : each = 2 agg + 2 edge + 4 hosts

Link counts:
  - Core → Agg  : 16  (each core connects to 1 agg per pod)
  - Agg  → Edge : 16  (full bipartite within each pod)
  - Edge → Host : 16
  Total         : 48

Bandwidth tiers:
  - Core ↔ Agg  : 10 Gbps
  - Agg  ↔ Edge : 1  Gbps
  - Edge ↔ Host : 100 Mbps

OpenFlow13 / Remote Ryu controller at 127.0.0.1:6633
"""

import os
import sys

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from topology_data.abilene_imp import SWITCH_TO_DPID


def should_run_traffic() -> bool:
    value = os.environ.get("AUTO_TRAFFIC", "1")
    return value.strip().lower() in ("1", "true", "yes", "on")


def build_topology():
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True,
    )

    info("*** Adding remote Ryu controller\n")
    net.addController(
        "ctrl",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6633,
    )

    # ------------------------------------------------------------------ #
    #  Core layer  (c0 – c3)                                              #
    # ------------------------------------------------------------------ #
    info("*** Adding core switches (c0–c3)\n")
    core = {}
    for i in range(4):
        name = f"c{i}"
        core[name] = net.addSwitch(
            name,
            protocols="OpenFlow13",
            dpid=f"{SWITCH_TO_DPID[name]:016x}",
        )

    # ------------------------------------------------------------------ #
    #  Aggregation layer  (a1 – a8)                                       #
    # ------------------------------------------------------------------ #
    info("*** Adding aggregation switches (a1–a8)\n")
    agg = {}
    for i in range(1, 9):
        name = f"a{i}"
        agg[name] = net.addSwitch(
            name,
            protocols="OpenFlow13",
            dpid=f"{SWITCH_TO_DPID[name]:016x}",
        )

    # ------------------------------------------------------------------ #
    #  Edge layer  (e1 – e8)                                              #
    # ------------------------------------------------------------------ #
    info("*** Adding edge switches (e1–e8)\n")
    edge = {}
    for i in range(1, 9):
        name = f"e{i}"
        edge[name] = net.addSwitch(
            name,
            protocols="OpenFlow13",
            dpid=f"{SWITCH_TO_DPID[name]:016x}",
        )

    # ------------------------------------------------------------------ #
    #  Hosts  (h1 – h16)                                                  #
    # ------------------------------------------------------------------ #
    info("*** Adding hosts (h1–h16)\n")
    hosts = {}
    for i in range(1, 17):
        name = f"h{i}"
        hosts[name] = net.addHost(
            name,
            ip=f"10.0.0.{i}/24",
            mac=f"00:00:00:00:00:{i:02x}",
        )

    # ------------------------------------------------------------------ #
    #  Core → Aggregation links                                           #
    #                                                                     #
    #  Two core switches (c0, c1) attach to the FIRST agg switch of      #
    #  every pod (a1, a3, a5, a7).                                       #
    #  Two core switches (c2, c3) attach to the SECOND agg switch of     #
    #  every pod (a2, a4, a6, a8).                                       #
    #  This gives every pod two upward paths per agg switch.             #
    # ------------------------------------------------------------------ #
    info("*** Adding core–aggregation links\n")
    core_agg_links = [
        # c0 & c1  →  first agg of each pod
        ("c0", "a1"), ("c0", "a3"), ("c0", "a5"), ("c0", "a7"),
        ("c1", "a1"), ("c1", "a3"), ("c1", "a5"), ("c1", "a7"),
        # c2 & c3  →  second agg of each pod
        ("c2", "a2"), ("c2", "a4"), ("c2", "a6"), ("c2", "a8"),
        ("c3", "a2"), ("c3", "a4"), ("c3", "a6"), ("c3", "a8"),
    ]

    all_sw = {**core, **agg, **edge}
    for left, right in core_agg_links:
        net.addLink(
            all_sw[left],
            all_sw[right],
            bw=10000,
            delay="1ms",
            r2q=100000,
        )

    # ------------------------------------------------------------------ #
    #  Aggregation → Edge links  (full bipartite within each pod)         #
    #                                                                     #
    #  Pod 0 : a1, a2  ↔  e1, e2                                        #
    #  Pod 1 : a3, a4  ↔  e3, e4                                        #
    #  Pod 2 : a5, a6  ↔  e5, e6                                        #
    #  Pod 3 : a7, a8  ↔  e7, e8                                        #
    # ------------------------------------------------------------------ #
    info("*** Adding aggregation–edge links\n")
    agg_edge_links = [
        # Pod 0
        ("a1", "e1"), ("a1", "e2"),
        ("a2", "e1"), ("a2", "e2"),
        # Pod 1
        ("a3", "e3"), ("a3", "e4"),
        ("a4", "e3"), ("a4", "e4"),
        # Pod 2
        ("a5", "e5"), ("a5", "e6"),
        ("a6", "e5"), ("a6", "e6"),
        # Pod 3
        ("a7", "e7"), ("a7", "e8"),
        ("a8", "e7"), ("a8", "e8"),
    ]

    for left, right in agg_edge_links:
        net.addLink(
            all_sw[left],
            all_sw[right],
            bw=1000,
            delay="1ms",
            r2q=10000,
        )

    # ------------------------------------------------------------------ #
    #  Edge → Host links  (2 hosts per edge switch)                       #
    # ------------------------------------------------------------------ #
    info("*** Adding edge–host links\n")
    edge_host_links = [
        ("e1", "h1"),  ("e1", "h2"),
        ("e2", "h3"),  ("e2", "h4"),
        ("e3", "h5"),  ("e3", "h6"),
        ("e4", "h7"),  ("e4", "h8"),
        ("e5", "h9"),  ("e5", "h10"),
        ("e6", "h11"), ("e6", "h12"),
        ("e7", "h13"), ("e7", "h14"),
        ("e8", "h15"), ("e8", "h16"),
    ]

    for sw_name, h_name in edge_host_links:
        net.addLink(
            all_sw[sw_name],
            hosts[h_name],
            bw=100,
            delay="1ms",
            r2q=1000,
        )

    # ------------------------------------------------------------------ #
    #  Summary                                                            #
    # ------------------------------------------------------------------ #
    total_switches = len(core) + len(agg) + len(edge)   # 20
    total_hosts    = len(hosts)                           # 16
    total_nodes    = total_switches + total_hosts         # 36
    total_links    = (len(core_agg_links)
                      + len(agg_edge_links)
                      + len(edge_host_links))             # 48

    info("*** Topology summary\n")
    info(f"    Name            : Fat-Tree k=4\n")
    info(f"    Core switches   : {len(core)}\n")
    info(f"    Agg  switches   : {len(agg)}\n")
    info(f"    Edge switches   : {len(edge)}\n")
    info(f"    Total switches  : {total_switches}\n")
    info(f"    Hosts           : {total_hosts}\n")
    info(f"    Total nodes     : {total_nodes}\n")
    info(f"    Core–Agg  links : {len(core_agg_links)}\n")
    info(f"    Agg–Edge  links : {len(agg_edge_links)}\n")
    info(f"    Edge–Host links : {len(edge_host_links)}\n")
    info(f"    Total links     : {total_links}\n")

    info("*** Starting network\n")
    net.start()

    info("*** Network ready\n")
    if should_run_traffic():
        info("*** Starting traffic generation (AUTO_TRAFFIC=0 to skip)\n")
        try:
            from traffic.generate_traffic import run_from_env

            results = run_from_env(net)
            info(f"*** Traffic complete: flows={len(results)}\n")
        except Exception as exc:
            info(f"*** Traffic generation failed: {exc}\n")

    info("*** Useful commands: nodes, links, net, pingall\n")
    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    build_topology()
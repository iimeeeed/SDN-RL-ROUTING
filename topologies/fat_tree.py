#!/usr/bin/env python3

"""
Strict k=4 Fat Tree topology for Mininet.

k = 4

Switches:
- 4 core switches        s1-s4
- 8 aggregation switches s5-s12
- 8 edge switches        s13-s20

Hosts:
- 16 hosts              h1-h16
- 2 hosts per edge switch

Controller:
- Remote Ryu controller at 127.0.0.1:6633

OpenFlow:
- OpenFlow13
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info


def build_topology():
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True
    )

    info("*** Adding remote Ryu controller\n")
    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6633
    )

    info("*** Adding strict k=4 Fat Tree switches\n")
    switches = {}

    for i in range(1, 21):
        switch_name = f"s{i}"
        switches[switch_name] = net.addSwitch(
            switch_name,
            protocols="OpenFlow13"
        )

    core_switches = [f"s{i}" for i in range(1, 5)]
    agg_switches = [f"s{i}" for i in range(5, 13)]
    edge_switches = [f"s{i}" for i in range(13, 21)]

    info(f"    Core switches: {core_switches}\n")
    info(f"    Aggregation switches: {agg_switches}\n")
    info(f"    Edge switches: {edge_switches}\n")

    info("*** Adding hosts\n")
    hosts = {}

    for i in range(1, 17):
        host_name = f"h{i}"
        hosts[host_name] = net.addHost(
            host_name,
            ip=f"10.0.0.{i}/24",
            mac=f"00:00:00:00:00:{i:02x}"
        )

    info("*** Adding host-edge links\n")

    host_links = [
        ("h1", "s13"), ("h2", "s13"),
        ("h3", "s14"), ("h4", "s14"),

        ("h5", "s15"), ("h6", "s15"),
        ("h7", "s16"), ("h8", "s16"),

        ("h9", "s17"), ("h10", "s17"),
        ("h11", "s18"), ("h12", "s18"),

        ("h13", "s19"), ("h14", "s19"),
        ("h15", "s20"), ("h16", "s20"),
    ]

    for host, edge in host_links:
        net.addLink(
            hosts[host],
            switches[edge],
            bw=100,
            delay="1ms"
        )

    info("*** Adding strict k=4 Fat Tree switch links\n")

    switch_links = [
        # Pod 0 edge-aggregation links
        ("s13", "s5"), ("s13", "s6"),
        ("s14", "s5"), ("s14", "s6"),

        # Pod 1 edge-aggregation links
        ("s15", "s7"), ("s15", "s8"),
        ("s16", "s7"), ("s16", "s8"),

        # Pod 2 edge-aggregation links
        ("s17", "s9"), ("s17", "s10"),
        ("s18", "s9"), ("s18", "s10"),

        # Pod 3 edge-aggregation links
        ("s19", "s11"), ("s19", "s12"),
        ("s20", "s11"), ("s20", "s12"),

        # Aggregation-core links: pod 0
        ("s5", "s1"), ("s5", "s2"),
        ("s6", "s3"), ("s6", "s4"),

        # Aggregation-core links: pod 1
        ("s7", "s1"), ("s7", "s2"),
        ("s8", "s3"), ("s8", "s4"),

        # Aggregation-core links: pod 2
        ("s9", "s1"), ("s9", "s2"),
        ("s10", "s3"), ("s10", "s4"),

        # Aggregation-core links: pod 3
        ("s11", "s1"), ("s11", "s2"),
        ("s12", "s3"), ("s12", "s4"),
    ]

    for left, right in switch_links:
        net.addLink(
            switches[left],
            switches[right],
            bw=1000,
            delay="2ms"
        )

    total_nodes = len(switches) + len(hosts)
    total_links = len(host_links) + len(switch_links)

    info("*** Topology summary\n")
    info(f"    k: 4\n")
    info(f"    Core switches: {len(core_switches)}\n")
    info(f"    Aggregation switches: {len(agg_switches)}\n")
    info(f"    Edge switches: {len(edge_switches)}\n")
    info(f"    Total switches: {len(switches)}\n")
    info(f"    Hosts: {len(hosts)}\n")
    info(f"    Total nodes: {total_nodes}\n")
    info(f"    Host links: {len(host_links)}\n")
    info(f"    Switch links: {len(switch_links)}\n")
    info(f"    Total links: {total_links}\n")

    info("*** Starting network\n")
    net.start()

    info("*** Network ready\n")
    info("*** Useful Mininet commands:\n")
    info("    nodes\n")
    info("    links\n")
    info("    net\n")
    info("    pingall\n")

    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    build_topology()

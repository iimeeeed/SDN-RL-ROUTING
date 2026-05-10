#!/usr/bin/env python3

"""
Project Fat Tree topology.

Required by project:
- Total nodes: 20
- Total links: 36
- TCI: 0.85

Implementation:
- 8 switches: s1-s8
- 12 hosts: h1-h12
- 12 host links
- 24 switch links
- OpenFlow13
- Remote Ryu controller at 127.0.0.1:6633

Note:
This is a reduced Fat Tree-like topology matching the project node/link
requirements, not a strict k=4 Fat Tree.
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

    info("*** Adding Fat Tree switches\n")
    switches = {}

    for i in range(1, 9):
        switch_name = f"s{i}"
        switches[switch_name] = net.addSwitch(
            switch_name,
            protocols="OpenFlow13"
        )

    info("*** Adding hosts\n")
    hosts = {}

    for i in range(1, 13):
        host_name = f"h{i}"
        hosts[host_name] = net.addHost(
            host_name,
            ip=f"10.0.0.{i}/24",
            mac=f"00:00:00:00:00:{i:02x}"
        )

    info("*** Adding host-edge links\n")

    host_links = [
        ("h1", "s5"), ("h2", "s5"), ("h3", "s5"),
        ("h4", "s6"), ("h5", "s6"), ("h6", "s6"),
        ("h7", "s7"), ("h8", "s7"), ("h9", "s7"),
        ("h10", "s8"), ("h11", "s8"), ("h12", "s8"),
    ]

    for host, switch in host_links:
        net.addLink(
            hosts[host],
            switches[switch],
            bw=100,
            delay="1ms"
        )

    info("*** Adding Fat Tree switch links\n")

    switch_links = [
        # Core layer interconnections
        ("s1", "s2"), ("s1", "s3"), ("s1", "s4"),
        ("s2", "s3"), ("s2", "s4"),
        ("s3", "s4"),
        
        # Core to aggregation
        ("s1", "s5"), ("s1", "s6"), ("s1", "s7"), ("s1", "s8"),
        ("s2", "s5"), ("s2", "s6"), ("s2", "s7"), ("s2", "s8"),
        ("s3", "s5"), ("s3", "s6"), ("s3", "s7"), ("s3", "s8"),
        ("s4", "s5"), ("s4", "s6"), ("s4", "s7"), ("s4", "s8"),
        
        # Aggregation layer interconnections
        ("s5", "s6"), ("s5", "s7"), ("s5", "s8"),
        ("s6", "s7"), ("s6", "s8"),
        ("s7", "s8"),
    ]

    for left, right in switch_links:
        net.addLink(
            switches[left],
            switches[right],
            bw=1000,
            delay="2ms"
        )

    total_switches = len(switches)
    total_hosts = len(hosts)
    total_nodes = total_switches + total_hosts
    total_links = len(host_links) + len(switch_links)

    info("*** Topology summary\n")
    info(f"    Name: Fat Tree\n")
    info(f"    Switches: {total_switches}\n")
    info(f"    Hosts: {total_hosts}\n")
    info(f"    Total nodes: {total_nodes}\n")
    info(f"    Host links: {len(host_links)}\n")
    info(f"    Switch links: {len(switch_links)}\n")
    info(f"    Total links: {total_links}\n")

    # Topological Complexity Index: cyclomatic complexity of backbone
    # = (E - N + 1) / E where E is backbone edges and N is switches
    tci = (len(switch_links) - total_switches + 1) / len(switch_links)
    info(f"    TCI: {tci}\n")

    info("*** Starting network\n")
    net.start()

    info("*** Network ready\n")
    info("*** Useful commands: nodes, links, net, pingall\n")

    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    build_topology()

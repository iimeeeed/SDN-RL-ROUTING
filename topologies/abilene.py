#!/usr/bin/env python3

"""
Project Abilene topology.

Required by project:
- Total nodes: 12
- Total links: 15
- TCI: 0.58

Implementation:
- 7 switches: s1-s7
- 5 hosts: h1-h5
- 5 host links
- 10 backbone links
- OpenFlow13
- Remote Ryu controller at 127.0.0.1:6633
"""

import os

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info


def env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def build_topology():
    host_bw = env_int(
        "TOPO_HOST_BW_MBPS",
        env_int("TRAFFIC_LINK_BW_Mbps", 100),
    )
    backbone_bw = env_int("TOPO_BACKBONE_BW_MBPS", 1000)

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

    info("*** Adding Abilene switches\n")
    switches = {}

    for i in range(1, 8):
        switch_name = f"s{i}"
        switches[switch_name] = net.addSwitch(
            switch_name,
            protocols="OpenFlow13"
        )

    info("*** Adding hosts\n")
    hosts = {}

    for i in range(1, 6):
        host_name = f"h{i}"
        hosts[host_name] = net.addHost(
            host_name,
            ip=f"10.0.0.{i}/24",
            mac=f"00:00:00:00:00:{i:02x}"
        )

    info("*** Adding host-switch links\n")

    host_links = [
        ("h1", "s1"),
        ("h2", "s2"),
        ("h3", "s4"),
        ("h4", "s6"),
        ("h5", "s7"),
    ]

    for host, switch in host_links:
        net.addLink(
            hosts[host],
            switches[switch],
            bw=host_bw,
            delay="1ms",
            r2q=10000
        )

    info("*** Adding Abilene backbone links\n")

    backbone_links = [
        ("s1", "s2"),
        ("s1", "s3"),
        ("s1", "s4"),
        ("s2", "s3"),
        ("s2", "s4"),
        ("s2", "s5"),
        ("s3", "s5"),
        ("s3", "s6"),
        ("s4", "s5"),
        ("s4", "s6"),
        ("s4", "s7"),
        ("s5", "s6"),
        ("s5", "s7"),
        ("s6", "s7"),
    ]

    for left, right in backbone_links:
        net.addLink(
            switches[left],
            switches[right],
            bw=backbone_bw,
            delay="3ms",
            r2q=100000
        )

    total_switches = len(switches)
    total_hosts = len(hosts)
    total_nodes = total_switches + total_hosts
    total_links = len(host_links) + len(backbone_links)

    info("*** Topology summary\n")
    info(f"    Name: Abilene\n")
    info(f"    Switches: {total_switches}\n")
    info(f"    Hosts: {total_hosts}\n")
    info(f"    Total nodes: {total_nodes}\n")
    info(f"    Host links: {len(host_links)}\n")
    info(f"    Backbone links: {len(backbone_links)}\n")
    info(f"    Total links: {total_links}\n")
    info(f"    Host bw: {host_bw} Mbps\n")
    info(f"    Backbone bw: {backbone_bw} Mbps\n")

    # Topological Complexity Index: cyclomatic complexity of backbone
    # = (E - N + 1) / E where E is backbone edges and N is switches
    tci = (len(backbone_links) - total_switches + 1) / len(backbone_links)
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

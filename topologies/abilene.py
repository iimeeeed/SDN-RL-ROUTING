#!/usr/bin/env python3

"""
Realistic Abilene/Internet2 backbone topology for the SDN RL routing project.

This topology is based on the commonly used Abilene backbone graph with
11 backbone router/city nodes:

- Seattle
- Sunnyvale
- Los Angeles
- Denver
- Kansas City
- Houston
- Chicago
- Indianapolis
- Atlanta
- Washington
- New York

Each backbone router has one attached host for traffic generation.

Notes:
- This is closer to the real Abilene backbone than the earlier simplified
  7-switch / 5-host topology.
- The switch names are short Mininet names: s1, s2, ...
- The city names are stored in comments and dictionaries for readability.
- OpenFlow13 is used because the validated local Ryu/Mininet setup works
  with ryu.app.simple_switch_13 and OpenFlow13.
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info


# Switch ID to city mapping.
# These correspond to Abilene backbone POP/router locations.
CITY_BY_SWITCH = {
    "s1": "Seattle",
    "s2": "Sunnyvale",
    "s3": "LosAngeles",
    "s4": "Denver",
    "s5": "KansasCity",
    "s6": "Houston",
    "s7": "Chicago",
    "s8": "Indianapolis",
    "s9": "Atlanta",
    "s10": "Washington",
    "s11": "NewYork",
}


def build_topology():
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True
    )

    info("*** Adding remote Ryu controller\n")
    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6633
    )

    info("*** Adding Abilene backbone switches\n")
    switches = {}

    for switch_name, city in CITY_BY_SWITCH.items():
        switches[switch_name] = net.addSwitch(
            switch_name,
            protocols="OpenFlow13"
        )
        info(f"    {switch_name}: {city}\n")

    info("*** Adding one host per backbone router\n")
    hosts = {}

    for i, switch_name in enumerate(CITY_BY_SWITCH.keys(), start=1):
        host_name = f"h{i}"
        hosts[host_name] = net.addHost(
            host_name,
            ip=f"10.0.0.{i}/24",
            mac=f"00:00:00:00:00:{i:02x}"
        )

        # One host connected to each city/backbone switch.
        net.addLink(
            hosts[host_name],
            switches[switch_name],
            bw=100,
            delay="1ms"
        )

    info("*** Adding Abilene backbone links\n")

    # Backbone links.
    #
    # Format:
    # (switch_a, switch_b, isis_weight)
    #
    # The weights are kept as link parameters and can later be reused by the
    # Dijkstra/RL controller logic.
    #
    # This graph follows the commonly used 11-node Abilene backbone structure.
    backbone_links = [
        # West coast / mountain
        ("s1", "s2", 1295),     # Seattle - Sunnyvale
        ("s2", "s3", 366),      # Sunnyvale - Los Angeles
        ("s2", "s4", 1295),     # Sunnyvale - Denver
        ("s3", "s6", 1893),     # Los Angeles - Houston

        # Central backbone
        ("s4", "s5", 587),      # Denver - Kansas City
        ("s5", "s6", 902),      # Kansas City - Houston
        ("s5", "s8", 587),      # Kansas City - Indianapolis

        # North / east
        ("s1", "s7", 2095),     # Seattle - Chicago
        ("s7", "s8", 260),      # Chicago - Indianapolis
        ("s7", "s11", 639),     # Chicago - New York

        # South / east
        ("s6", "s9", 1176),     # Houston - Atlanta
        ("s8", "s9", 1176),     # Indianapolis - Atlanta
        ("s9", "s10", 846),     # Atlanta - Washington
        ("s10", "s11", 233),    # Washington - New York
    ]

    for left, right, weight in backbone_links:
        # Higher IS-IS weight roughly corresponds to longer distance.
        # We also convert weight into a simple Mininet delay estimate.
        delay_ms = max(2, int(weight / 300))

        net.addLink(
            switches[left],
            switches[right],
            bw=1000,
            delay=f"{delay_ms}ms",
            weight=weight
        )

        info(
            f"    {left}({CITY_BY_SWITCH[left]}) "
            f"<-> {right}({CITY_BY_SWITCH[right]}), "
            f"weight={weight}, delay={delay_ms}ms\n"
        )

    total_nodes = len(switches) + len(hosts)
    total_links = len(backbone_links) + len(hosts)

    info("*** Topology summary\n")
    info(f"    Backbone switches: {len(switches)}\n")
    info(f"    Hosts: {len(hosts)}\n")
    info(f"    Total nodes: {total_nodes}\n")
    info(f"    Backbone links: {len(backbone_links)}\n")
    info(f"    Host links: {len(hosts)}\n")
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

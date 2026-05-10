#!/usr/bin/env python3

"""
Generic topology-aware Dijkstra controller.

Usage:
    TOPOLOGY=abilene ryu-manager controllers/dijkstra.py
    TOPOLOGY=fat_tree ryu-manager controllers/dijkstra.py

This controller:
- loads topology data from topology_data/<name>.py
- builds a NetworkX graph
- computes shortest paths using Dijkstra
- installs OpenFlow 1.3 rules
- avoids uncontrolled flooding in looped topologies
"""

import os
import sys



import importlib
import networkx as nx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3

from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import arp


class DijkstraController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(DijkstraController, self).__init__(*args, **kwargs)

        self.datapaths = {}

        topology_name = os.environ.get("TOPOLOGY", "abilene")
        module_name = f"topology_data.{topology_name}"

        self.topology = self.load_topology_module(module_name)

        self.graph = nx.Graph()
        self.graph.add_nodes_from(self.topology.SWITCHES)

        for u, v, weight in self.topology.BACKBONE_LINKS:
            self.graph.add_edge(u, v, weight=weight)

        self.host_to_switch = self.topology.HOST_TO_SWITCH
        self.host_to_ip = self.topology.HOST_TO_IP
        self.host_to_mac = self.topology.HOST_TO_MAC
        self.port_map = self.topology.PORT_MAP

        self.dpid_to_switch_map = getattr(self.topology, "DPID_TO_SWITCH", None)
        self.switch_to_dpid_map = getattr(self.topology, "SWITCH_TO_DPID", None)

        self.mac_to_host = {
            mac: host for host, mac in self.host_to_mac.items()
        }

        self.ip_to_host = {
            ip: host for host, ip in self.host_to_ip.items()
        }

        self.learned_flow_idle_timeout = int(
            os.environ.get("LEARNED_FLOW_IDLE_TIMEOUT", "1")
        )

        self.logger.info("Loaded topology: %s", topology_name)
        self.logger.info("Switches: %s", self.topology.SWITCHES)
        self.logger.info("Hosts: %s", list(self.host_to_switch.keys()))
        self.logger.info(
            "Dijkstra controller ready learned_flow_idle_timeout=%s",
            self.learned_flow_idle_timeout
        )

    def load_topology_module(self, module_name):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != "topology_data":
                raise

            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            if project_root not in sys.path:
                sys.path.insert(0, project_root)

            return importlib.import_module(module_name)

    def dpid_to_switch(self, dpid):
        if self.dpid_to_switch_map:
            switch_name = self.dpid_to_switch_map.get(dpid)
            if switch_name:
                return switch_name
        return f"s{dpid}"

    def switch_to_dpid(self, switch_name):
        if self.switch_to_dpid_map:
            if switch_name in self.switch_to_dpid_map:
                return self.switch_to_dpid_map[switch_name]
            raise KeyError(f"Unknown switch name: {switch_name}")
        return int(switch_name.replace("s", ""))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        self.add_flow(
            datapath=datapath,
            priority=0,
            match=match,
            actions=actions,
            idle_timeout=0
        )

        self.logger.info("Switch connected: dpid=%s", datapath.id)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=60):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions,
            idle_timeout=idle_timeout,
            hard_timeout=0
        )

        datapath.send_msg(mod)

    def packet_out(self, datapath, msg, out_port):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        actions = [parser.OFPActionOutput(out_port)]

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=msg.match["in_port"],
            actions=actions,
            data=data
        )

        datapath.send_msg(out)

    def get_shortest_switch_path(self, src_host, dst_host):
        src_switch = self.host_to_switch[src_host]
        dst_switch = self.host_to_switch[dst_host]

        return nx.shortest_path(
            self.graph,
            source=src_switch,
            target=dst_switch,
            weight="weight"
        )

    def get_out_port(self, current_switch, next_node):
        try:
            return self.port_map[current_switch][next_node]
        except KeyError:
            self.logger.error(
                "No port mapping for current_switch=%s next_node=%s",
                current_switch,
                next_node
            )
            raise

    def install_path(self, src_host, dst_host):
        dst_mac = self.host_to_mac[dst_host]
        switch_path = self.get_shortest_switch_path(src_host, dst_host)

        self.logger.info(
            "Installing path %s -> %s: %s",
            src_host,
            dst_host,
            " -> ".join(switch_path)
        )

        for index, current_switch in enumerate(switch_path):
            current_dpid = self.switch_to_dpid(current_switch)

            if current_dpid not in self.datapaths:
                self.logger.warning(
                    "Switch %s not connected yet",
                    current_switch
                )
                continue

            datapath = self.datapaths[current_dpid]
            parser = datapath.ofproto_parser

            if index == len(switch_path) - 1:
                out_port = self.get_out_port(current_switch, dst_host)
            else:
                next_switch = switch_path[index + 1]
                out_port = self.get_out_port(current_switch, next_switch)

            actions = [parser.OFPActionOutput(out_port)]
            match = parser.OFPMatch(eth_dst=dst_mac)

            self.add_flow(
                datapath=datapath,
                priority=10,
                match=match,
                actions=actions,
                idle_timeout=self.learned_flow_idle_timeout
            )

    def install_bidirectional_path(self, host_a, host_b):
        self.install_path(host_a, host_b)
        self.install_path(host_b, host_a)

    def resolve_arp_destination(self, arp_pkt):
        if arp_pkt is None:
            return None

        return self.ip_to_host.get(arp_pkt.dst_ip)

    def forward_current_packet(self, msg, src_host, dst_host):
        datapath = msg.datapath
        current_switch = self.dpid_to_switch(datapath.id)

        switch_path = self.get_shortest_switch_path(src_host, dst_host)

        if current_switch not in switch_path:
            self.logger.warning(
                "Current switch %s not in path %s",
                current_switch,
                switch_path
            )
            return

        index = switch_path.index(current_switch)

        if index == len(switch_path) - 1:
            out_port = self.get_out_port(current_switch, dst_host)
        else:
            next_switch = switch_path[index + 1]
            out_port = self.get_out_port(current_switch, next_switch)

        self.packet_out(datapath, msg, out_port)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        pkt = packet.Packet(msg.data)

        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # Silently drop IPv6 multicast and broadcast — not handled by this controller
        if eth.dst.startswith("33:33:") or eth.dst == "ff:ff:ff:ff:ff:ff":
            self.logger.debug("Dropping IPv6 multicast/broadcast packet")
            return

        src_mac = eth.src
        dst_mac = eth.dst

        src_host = self.mac_to_host.get(src_mac)
        dst_host = self.mac_to_host.get(dst_mac)

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            arp_pkt = pkt.get_protocol(arp.arp)

            if src_host is None:
                self.logger.info("Unknown ARP source MAC: %s", src_mac)
                return

            dst_host = self.resolve_arp_destination(arp_pkt)

            if dst_host is None:
                self.logger.info("Unknown ARP destination IP: %s", arp_pkt.dst_ip)
                return

            self.logger.info("Handling ARP packet: %s -> %s", src_host, dst_host)

            self.install_bidirectional_path(src_host, dst_host)
            self.forward_current_packet(msg, src_host, dst_host)
            return

        if src_host is None or dst_host is None:
            self.logger.info(
                "Unknown unicast mapping: src_mac=%s dst_mac=%s",
                src_mac,
                dst_mac
            )
            return

        self.logger.info("Handling unicast packet: %s -> %s", src_host, dst_host)

        self.install_bidirectional_path(src_host, dst_host)
        self.forward_current_packet(msg, src_host, dst_host)

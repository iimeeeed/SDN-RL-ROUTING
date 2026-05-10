#!/usr/bin/env python3

"""
Equal-Cost Multi-Path (ECMP) baseline controller (OpenFlow 1.3).

Splits traffic across all valid equal-cost next hops toward the destination
attachment switch (same shortest-path distance as Dijkstra), using a SELECT
group so different flows hash to different buckets.

Usage:
    TOPOLOGY=abilene ryu-manager controllers/ecmp.py
    TOPOLOGY=fat_tree ryu-manager controllers/ecmp.py

Behaviour mirrors controllers/dijkstra.py for topology loading and packet_in,
but installs SELECT groups where multiple next hops qualify as ECMP.
"""

from __future__ import annotations

import importlib
import math
import os
import sys
import zlib

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


class ECMPController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ECMPController, self).__init__(*args, **kwargs)

        self.datapaths: dict = {}

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

        self.mac_to_host = {mac: host for host, mac in self.host_to_mac.items()}
        self.ip_to_host = {ip: host for host, ip in self.host_to_ip.items()}

        # Cache: attachment_switch -> dict[node]distance_to_attachment
        self._dist_cache: dict[str, dict] = {}

        # Stable SELECT group ids per (datapath.id, eth_dst mac string)
        self._group_ids: dict[tuple[int, str], int] = {}
        self._next_group_id = 1

        # (datapath.id, group_id) successfully programmed
        self._groups_live: set[tuple[int, int]] = set()

        self.logger.info("Loaded topology: %s", topology_name)
        self.logger.info("Switches: %s", self.topology.SWITCHES)
        self.logger.info("Hosts: %s", list(self.host_to_switch.keys()))

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
        return f"s{dpid}"

    def switch_to_dpid(self, switch_name):
        return int(switch_name.replace("s", ""))

    def _distance_to_attachment(self, attach_sw: str) -> dict:
        if attach_sw not in self._dist_cache:
            self._dist_cache[attach_sw] = nx.single_source_dijkstra_path_length(
                self.graph, attach_sw, weight="weight"
            )
        return self._dist_cache[attach_sw]

    def ecmp_out_ports(self, current_switch: str, dst_host: str) -> list[int]:
        """Return output ports on current_switch that lie on some shortest path."""
        attach = self.host_to_switch[dst_host]
        dist = self._distance_to_attachment(attach)

        if current_switch == attach:
            return [self.get_out_port(current_switch, dst_host)]

        out_ports: list[int] = []
        du = dist[current_switch]

        for nbr in self.graph.neighbors(current_switch):
            w = self.graph[current_switch][nbr]["weight"]
            if math.isclose(dist[nbr] + w, du, rel_tol=0.0, abs_tol=1e-9):
                out_ports.append(self.get_out_port(current_switch, nbr))

        out_ports = sorted(set(out_ports))
        if not out_ports:
            raise RuntimeError(
                f"No ECMP next hops from {current_switch} toward {dst_host}"
            )
        return out_ports

    def alloc_group_id(self, datapath, dst_mac: str) -> int:
        key = (datapath.id, dst_mac)
        if key not in self._group_ids:
            self._group_ids[key] = self._next_group_id
            self._next_group_id += 1
        return self._group_ids[key]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ]

        self.add_flow(
            datapath=datapath,
            priority=0,
            match=match,
            actions=actions,
            idle_timeout=0,
        )

        self.logger.info("Switch connected: dpid=%s", datapath.id)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=60):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        instructions = [
            parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions,
            idle_timeout=idle_timeout,
            hard_timeout=0,
        )

        datapath.send_msg(mod)

    def add_flow_instructions(self, datapath, priority, match, instructions, idle_timeout=60):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions,
            idle_timeout=idle_timeout,
            hard_timeout=0,
        )

        datapath.send_msg(mod)

    def ensure_select_group(self, datapath, group_id: int, out_ports: list[int]):
        key = (datapath.id, group_id)
        if key in self._groups_live:
            return

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        buckets = []
        for port in out_ports:
            actions = [parser.OFPActionOutput(port)]
            buckets.append(parser.OFPBucket(weight=1, actions=actions))

        mod = parser.OFPGroupMod(
            datapath=datapath,
            command=ofproto.OFPGC_ADD,
            type_=ofproto.OFPGT_SELECT,
            group_id=group_id,
            buckets=buckets,
        )
        datapath.send_msg(mod)
        self._groups_live.add(key)

    def install_ecmp_rule_at_switch(self, switch_name: str, dst_host: str):
        dst_mac = self.host_to_mac[dst_host]
        dpid = self.switch_to_dpid(switch_name)

        if dpid not in self.datapaths:
            self.logger.warning("Switch %s not connected yet", switch_name)
            return

        datapath = self.datapaths[dpid]
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        out_ports = self.ecmp_out_ports(switch_name, dst_host)

        if len(out_ports) == 1:
            actions = [parser.OFPActionOutput(out_ports[0])]
            instructions = [
                parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
            ]
        else:
            gid = self.alloc_group_id(datapath, dst_mac)
            self.ensure_select_group(datapath, gid, out_ports)
            actions = [parser.OFPActionGroup(gid)]
            instructions = [
                parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
            ]

        match = parser.OFPMatch(eth_dst=dst_mac)
        self.add_flow_instructions(
            datapath=datapath,
            priority=10,
            match=match,
            instructions=instructions,
        )

        self.logger.info(
            "ECMP rule at %s toward %s ports=%s",
            switch_name,
            dst_host,
            out_ports,
        )

    def install_toward_host_network_wide(self, dst_host: str):
        """Install destination-specific forwarding at every connected switch."""
        for sw in self.topology.SWITCHES:
            self.install_ecmp_rule_at_switch(sw, dst_host)

    def install_bidirectional_ecmp(self, host_a: str, host_b: str):
        self.install_toward_host_network_wide(host_a)
        self.install_toward_host_network_wide(host_b)

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
            data=data,
        )

        datapath.send_msg(out)

    def get_out_port(self, current_switch, next_node):
        try:
            return self.port_map[current_switch][next_node]
        except KeyError:
            self.logger.error(
                "No port mapping for current_switch=%s next_node=%s",
                current_switch,
                next_node,
            )
            raise

    def forward_current_packet_ecmp(self, msg, src_host, dst_host):
        datapath = msg.datapath
        current_switch = self.dpid_to_switch(datapath.id)

        ports = self.ecmp_out_ports(current_switch, dst_host)
        dst_mac = self.host_to_mac[dst_host]
        idx = zlib.adler32(dst_mac.encode("ascii", errors="ignore")) % len(ports)
        out_port = ports[idx]

        self.packet_out(datapath, msg, out_port)

    def resolve_arp_destination(self, arp_pkt):
        if arp_pkt is None:
            return None

        return self.ip_to_host.get(arp_pkt.dst_ip)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        pkt = packet.Packet(msg.data)

        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
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

            self.install_bidirectional_ecmp(src_host, dst_host)
            self.forward_current_packet_ecmp(msg, src_host, dst_host)
            return

        if src_host is None or dst_host is None:
            self.logger.info(
                "Unknown unicast mapping: src_mac=%s dst_mac=%s",
                src_mac,
                dst_mac,
            )
            return

        self.install_bidirectional_ecmp(src_host, dst_host)
        self.forward_current_packet_ecmp(msg, src_host, dst_host)

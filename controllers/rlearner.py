#!/usr/bin/env python3

"""
R-Learner (Q-learning) controller for topology-aware SDN routing.

Usage:
	TOPOLOGY=abilene ryu-manager controllers/rlearner.py
	TOPOLOGY=fat_tree ryu-manager controllers/rlearner.py
"""

import os
import sys
import json
import math
import socket
import threading
import time
from collections import defaultdict

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


import importlib
import random
from typing import Dict, List, Tuple

import networkx as nx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import arp
from ryu.lib.packet import ipv4
from ryu.lib.packet import tcp
from ryu.lib.packet import udp

from exploration.schedule import build_epsilon_schedule
from reward.controller_reward import compute_controller_reward
from reward.qos_reward import QoSReward, WEIGHT_CONFIGS


Path = Tuple[str, ...]
State = Tuple[str, ...]


class RLearnerController(app_manager.RyuApp):
	OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

	def __init__(self, *args, **kwargs):
		super(RLearnerController, self).__init__(*args, **kwargs)

		self.datapaths: Dict[int, object] = {}

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
		self.port_to_link = self.build_port_to_link_map()

		self.mac_to_host = {
			mac: host for host, mac in self.host_to_mac.items()
		}
		self.ip_to_host = {
			ip: host for host, ip in self.host_to_ip.items()
		}

		self.alpha = float(os.environ.get("Q_ALPHA", "0.1"))
		self.epsilon_schedule = build_epsilon_schedule()
		self.epsilon_step = 0
		self.epsilon = self.epsilon_schedule.get_epsilon(self.epsilon_step)
		self.path_cutoff = int(os.environ.get("PATH_CUTOFF", "6"))
		self.max_candidate_paths = int(os.environ.get("MAX_CANDIDATE_PATHS", "8"))
		self.max_path_weight_delta = float(
			os.environ.get("MAX_PATH_WEIGHT_DELTA", "1")
		)
		self.filter_transit_host_switches = self.parse_transit_host_filter()
		self.action_selection = os.environ.get("RLEARNER_SELECTION", "ucb").lower()
		self.ucb_c = float(os.environ.get("UCB_C", "0.35"))
		self.path_overlap_penalty = float(
			os.environ.get("PATH_OVERLAP_PENALTY", "0.0")
		)
		self.path_utilization_penalty = float(
			os.environ.get("PATH_UTILIZATION_PENALTY", "0.0")
		)
		self.use_congestion_state = (
			os.environ.get("USE_CONGESTION_STATE", "0")
			.strip()
			.lower()
			in ("1", "true", "yes", "on")
		)
		self.warmup_trials_per_action = int(
			os.environ.get("WARMUP_TRIALS_PER_ACTION", "0")
		)
		self.action_reward_window = max(
			int(os.environ.get("ACTION_REWARD_WINDOW", "1")),
			1
		)
		self.port_stats_interval = float(os.environ.get("PORT_STATS_INTERVAL", "0"))
		self.link_capacity_mbps = float(os.environ.get("LINK_CAPACITY_MBPS", "1000"))
		self.utilization_bucket_size = float(
			os.environ.get("UTILIZATION_BUCKET_SIZE", "0.25")
		)
		self.active_flow_bucket_size = int(
			os.environ.get("ACTIVE_FLOW_BUCKET_SIZE", "2")
		)
		self.use_advantage_reward = (
			os.environ.get("USE_ADVANTAGE_REWARD", "1")
			.strip()
			.lower()
			in ("1", "true", "yes", "on")
		)
		self.reward_baseline_scope = (
			os.environ.get("REWARD_BASELINE_SCOPE", "pair")
			.strip()
			.lower()
		)
		self.advantage_reward_scale = float(
			os.environ.get("ADVANTAGE_REWARD_SCALE", "1.0")
		)
		self.bad_reward_direct_threshold = float(
			os.environ.get("BAD_REWARD_DIRECT_THRESHOLD", "-1.1")
		)
		self.reward_baseline_alpha = float(
			os.environ.get("REWARD_BASELINE_ALPHA", "0.05")
		)
		self.learned_flow_idle_timeout = int(
			os.environ.get("LEARNED_FLOW_IDLE_TIMEOUT", "1")
		)
		self.reward_mode = os.environ.get("REWARD_MODE", "binary")
		self.weighted_profile = os.environ.get("WEIGHTED_PROFILE", "balanced")
		self.feedback_host = os.environ.get("REWARD_FEEDBACK_HOST", "127.0.0.1")
		self.feedback_port = int(os.environ.get("REWARD_FEEDBACK_PORT", "9999"))
		self.flow_rule_priority = int(os.environ.get("LEARNED_FLOW_PRIORITY", "200"))
		self.baseline_window = int(os.environ.get("BASELINE_WINDOW", "20"))
		self.gamma = float(os.environ.get("Q_GAMMA", "0.8"))
		rtt_clip_raw = os.environ.get("QOS_RTT_CLIP_MS", "5000")
		self.qos_rtt_clip_ms = (
			float(rtt_clip_raw) if rtt_clip_raw.strip() else None
		)
		self.qos_reward_normalization = os.environ.get(
			"QOS_REWARD_NORMALIZATION",
			"absolute"
		).lower()

		self.q_table: Dict[State, Dict[Path, float]] = {}
		self.path_cache: Dict[Tuple[str, str], List[Path]] = {}
		self.action_counts = defaultdict(lambda: defaultdict(int))
		self.warmup_action_counts = defaultdict(lambda: defaultdict(int))
		self.state_counts = defaultdict(int)
		self.reward_baselines = {}
		self.action_reward_buffers = defaultdict(list)
		self.recent_pair_metrics = {}
		self.port_stats = {}
		self.port_utilization = defaultdict(float)
		self.host_switches = set(self.host_to_switch.values())
		self.filtered_transit_host_switches = self.build_filtered_transit_host_switches()
		self.qos_reward = QoSReward(
			weights=WEIGHT_CONFIGS[self.weighted_profile],
			window=self.baseline_window,
			rtt_clip_ms=self.qos_rtt_clip_ms,
			normalization=self.qos_reward_normalization
		)
		self.pending_feedback = defaultdict(list)
		self.feedback_metrics = {}
		self.active_weighted_paths = {}
		self.flow_keys_by_packet = {}
		self.pending_by_host_pair = defaultdict(set)
		self.expected_protocols = self.parse_expected_protocols(
			os.environ.get("TRAFFIC_PROTOCOLS", "both")
		)
		self.feedback_lock = threading.Lock()

		self.logger.info("Loaded topology: %s", topology_name)
		self.logger.info("Switches: %s", self.topology.SWITCHES)
		self.logger.info("Hosts: %s", list(self.host_to_switch.keys()))
		self.logger.info(
			"R-Learner config: alpha=%s epsilon_schedule=%s epsilon=%s path_cutoff=%s max_candidate_paths=%s max_path_weight_delta=%s filter_transit_host_switches=%s action_selection=%s ucb_c=%s path_overlap_penalty=%s path_utilization_penalty=%s use_congestion_state=%s warmup_trials_per_action=%s action_reward_window=%s port_stats_interval=%s link_capacity_mbps=%s use_advantage_reward=%s reward_baseline_scope=%s advantage_reward_scale=%s bad_reward_direct_threshold=%s reward_baseline_alpha=%s learned_flow_idle_timeout=%s reward_mode=%s weighted_profile=%s qos_reward_normalization=%s qos_rtt_clip_ms=%s",
			self.alpha,
			self.epsilon_schedule,
			self.epsilon,
			self.path_cutoff,
			self.max_candidate_paths,
			self.max_path_weight_delta,
			self.filter_transit_host_switches,
			self.action_selection,
			self.ucb_c,
			self.path_overlap_penalty,
			self.path_utilization_penalty,
			self.use_congestion_state,
			self.warmup_trials_per_action,
			self.action_reward_window,
			self.port_stats_interval,
			self.link_capacity_mbps,
			self.use_advantage_reward,
			self.reward_baseline_scope,
			self.advantage_reward_scale,
			self.bad_reward_direct_threshold,
			self.reward_baseline_alpha,
			self.learned_flow_idle_timeout,
			self.reward_mode,
			self.weighted_profile,
			self.qos_reward_normalization,
			self.qos_rtt_clip_ms
		)
		if self.reward_mode == "weighted":
			self.start_feedback_listener()
		if self.port_stats_interval > 0:
			self.monitor_thread = hub.spawn(self.port_stats_monitor)

	def parse_transit_host_filter(self) -> bool:
		raw = os.environ.get("FILTER_TRANSIT_HOST_SWITCHES", "auto")
		value = raw.strip().lower()
		if value in ("1", "true", "yes", "on"):
			return True
		if value in ("0", "false", "no", "off"):
			return False
		return not bool(getattr(self.topology, "HOST_SWITCHES_ARE_TRANSIT", False))

	def build_filtered_transit_host_switches(self) -> set:
		if not self.filter_transit_host_switches:
			return set()
		explicit = getattr(self.topology, "TRANSIT_HOST_SWITCH_FILTER", None)
		if explicit is not None:
			return set(explicit)
		return set(self.host_switches)

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

	def build_port_to_link_map(self) -> Dict[Tuple[int, int], Tuple[str, str]]:
		port_to_link = {}
		switches = set(self.topology.SWITCHES)
		for switch, neighbors in self.port_map.items():
			if switch not in switches:
				continue
			try:
				dpid = self.switch_to_dpid(switch)
			except KeyError:
				continue
			for neighbor, port_no in neighbors.items():
				if neighbor not in switches:
					continue
				port_to_link[(dpid, int(port_no))] = (switch, neighbor)
		return port_to_link

	def canonical_link(self, src_switch: str, dst_switch: str) -> Tuple[str, str]:
		return tuple(sorted((src_switch, dst_switch)))

	def parse_expected_protocols(self, raw: str) -> set:
		raw = (raw or "both").lower()
		if raw == "both":
			return {"tcp", "udp"}
		return {item.strip() for item in raw.split(",") if item.strip()}

	def packet_protocol(self, pkt) -> str:
		if pkt.get_protocol(tcp.tcp) is not None:
			return "tcp"
		if pkt.get_protocol(udp.udp) is not None:
			return "udp"
		return "other"

	def packet_transport_ports(self, pkt):
		tcp_pkt = pkt.get_protocol(tcp.tcp)
		if tcp_pkt is not None:
			return tcp_pkt.src_port, tcp_pkt.dst_port
		udp_pkt = pkt.get_protocol(udp.udp)
		if udp_pkt is not None:
			return udp_pkt.src_port, udp_pkt.dst_port
		return None, None

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

	def port_stats_monitor(self) -> None:
		while True:
			for datapath in list(self.datapaths.values()):
				self.request_port_stats(datapath)
			hub.sleep(self.port_stats_interval)

	def request_port_stats(self, datapath) -> None:
		parser = datapath.ofproto_parser
		ofproto = datapath.ofproto
		request = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
		datapath.send_msg(request)

	@set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
	def port_stats_reply_handler(self, ev):
		now = time.time()
		dpid = ev.msg.datapath.id
		for stat in ev.msg.body:
			key = (dpid, stat.port_no)
			link = self.port_to_link.get(key)
			if link is None:
				continue
			total_bytes = stat.tx_bytes + stat.rx_bytes
			previous = self.port_stats.get(key)
			self.port_stats[key] = (now, total_bytes)
			if previous is None:
				continue
			prev_time, prev_bytes = previous
			elapsed = max(now - prev_time, 1e-6)
			mbps = max((total_bytes - prev_bytes) * 8.0 / elapsed / 1_000_000.0, 0.0)
			utilization = mbps / max(self.link_capacity_mbps, 1.0)
			canonical = self.canonical_link(*link)
			self.port_utilization[canonical] = max(
				self.port_utilization[canonical] * 0.7,
				min(utilization, 2.0)
			)

	def delete_flows_by_match(self, match_builder) -> int:
		deleted = 0
		for datapath in list(self.datapaths.values()):
			parser = datapath.ofproto_parser
			ofproto = datapath.ofproto
			mod = parser.OFPFlowMod(
				datapath=datapath,
				command=ofproto.OFPFC_DELETE,
				out_port=ofproto.OFPP_ANY,
				out_group=ofproto.OFPG_ANY,
				match=match_builder(parser)
			)
			datapath.send_msg(mod)
			deleted += 1
		return deleted

	def invalidate_measured_flow(
		self,
		src_host: str,
		dst_host: str,
		flow_key,
		protocol: str = None,
		port: int = None,
	) -> None:
		if not src_host or not dst_host or not protocol or port is None:
			return

		def forward_match(parser):
			if protocol == "tcp":
				return parser.OFPMatch(
					eth_type=ether_types.ETH_TYPE_IP,
					ipv4_src=self.host_to_ip[src_host],
					ipv4_dst=self.host_to_ip[dst_host],
					ip_proto=6,
					tcp_dst=port
				)
			return parser.OFPMatch(
				eth_type=ether_types.ETH_TYPE_IP,
				ipv4_src=self.host_to_ip[src_host],
				ipv4_dst=self.host_to_ip[dst_host],
				ip_proto=17,
				udp_dst=port
			)

		def reverse_match(parser):
			if protocol == "tcp":
				return parser.OFPMatch(
					eth_type=ether_types.ETH_TYPE_IP,
					ipv4_src=self.host_to_ip[dst_host],
					ipv4_dst=self.host_to_ip[src_host],
					ip_proto=6,
					tcp_src=port
				)
			return parser.OFPMatch(
				eth_type=ether_types.ETH_TYPE_IP,
				ipv4_src=self.host_to_ip[dst_host],
				ipv4_dst=self.host_to_ip[src_host],
				ip_proto=17,
				udp_src=port
			)

		forward_deletes = self.delete_flows_by_match(forward_match)
		reverse_deletes = self.delete_flows_by_match(reverse_match)
		self.logger.info(
			"Invalidated measured flow rules key=%s hosts=%s<->%s protocol=%s port=%s datapaths=%s",
			flow_key,
			src_host,
			dst_host,
			protocol,
			port,
			max(forward_deletes, reverse_deletes)
		)

	def add_flow(self, datapath, priority, match, actions, idle_timeout=0):
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

	def flow_match(
		self,
		parser,
		src_host: str,
		dst_host: str,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	):
		if protocol == "tcp" and src_port is not None and dst_port is not None:
			return parser.OFPMatch(
				eth_type=ether_types.ETH_TYPE_IP,
				ipv4_src=self.host_to_ip[src_host],
				ipv4_dst=self.host_to_ip[dst_host],
				ip_proto=6,
				tcp_src=src_port,
				tcp_dst=dst_port
			)
		if protocol == "udp" and src_port is not None and dst_port is not None:
			return parser.OFPMatch(
				eth_type=ether_types.ETH_TYPE_IP,
				ipv4_src=self.host_to_ip[src_host],
				ipv4_dst=self.host_to_ip[dst_host],
				ip_proto=17,
				udp_src=src_port,
				udp_dst=dst_port
			)
		return parser.OFPMatch(eth_dst=self.host_to_mac[dst_host])

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

	def get_all_paths(self, src_switch, dst_switch) -> List[Path]:
		state = (src_switch, dst_switch)
		if state in self.path_cache:
			return self.path_cache[state]

		if src_switch == dst_switch:
			paths = [(src_switch,)]
		else:
			all_paths = [
				tuple(path)
				for path in nx.all_simple_paths(
					self.graph,
					source=src_switch,
					target=dst_switch,
					cutoff=self.path_cutoff
				)
			]
			paths = self.filter_candidate_paths(
				all_paths,
				src_switch,
				dst_switch
			)
			if self.max_candidate_paths > 0:
				paths = paths[:self.max_candidate_paths]

		self.path_cache[state] = paths
		return paths

	def filter_candidate_paths(
		self,
		paths: List[Path],
		src_switch: str,
		dst_switch: str,
	) -> List[Path]:
		if not paths:
			return []

		candidates = list(paths)
		if self.filter_transit_host_switches:
			filtered = [
				path for path in candidates
				if not self.has_transit_host_switch(path, src_switch, dst_switch)
			]
			if len(filtered) >= 2:
				candidates = filtered

		if self.max_path_weight_delta >= 0:
			min_weight = min(self.path_weight(path) for path in candidates)
			near_shortest = [
				path for path in candidates
				if self.path_weight(path) <= min_weight + self.max_path_weight_delta
			]
			if near_shortest:
				candidates = near_shortest

		return sorted(
			candidates,
			key=lambda path: (
				self.path_weight(path),
				len(path),
				path
			)
		)

	def has_transit_host_switch(
		self,
		path: Path,
		src_switch: str,
		dst_switch: str,
	) -> bool:
		for switch in path[1:-1]:
			if (
				switch in self.filtered_transit_host_switches
				and switch not in (src_switch, dst_switch)
			):
				return True
		return False

	def path_weight(self, path: Path) -> float:
		if len(path) < 2:
			return 0.0
		return sum(
			self.graph[path[index]][path[index + 1]].get("weight", 1)
			for index in range(len(path) - 1)
		)

	def q_state(self, src_host: str, dst_host: str) -> State:
		if not self.use_congestion_state:
			return (src_host, dst_host)

		src_switch = self.host_to_switch[src_host]
		dst_switch = self.host_to_switch[dst_host]
		paths = self.get_all_paths(src_switch, dst_switch)
		max_util = max(
			(self.path_max_utilization(path) for path in paths),
			default=0.0
		)
		active_flows = max(
			(self.path_active_flow_count(path, {src_host, dst_host}) for path in paths),
			default=0
		)
		metrics = self.recent_pair_metrics.get(self.host_pair_key(src_host, dst_host), {})
		return (
			src_host,
			dst_host,
			f"util{self.bucket(max_util, self.utilization_bucket_size, 4)}",
			f"active{self.bucket(active_flows, self.active_flow_bucket_size, 4)}",
			f"rtt{self.bucket(metrics.get('rtt_ms', 0.0), 50.0, 6)}",
			f"loss{self.bucket(metrics.get('plr_pct', 0.0), 0.5, 6)}",
		)

	def state_switches(self, state: State):
		return self.host_to_switch[state[0]], self.host_to_switch[state[1]]

	def bucket(self, value: float, size: float, max_bucket: int) -> int:
		if size <= 0:
			return 0
		return min(int(value / size), max_bucket)

	def path_edges(self, path: Path) -> set:
		return {
			self.canonical_link(path[index], path[index + 1])
			for index in range(len(path) - 1)
		}

	def path_max_utilization(self, path: Path) -> float:
		if len(path) < 2:
			return 0.0
		return max(
			(self.port_utilization.get(edge, 0.0) for edge in self.path_edges(path)),
			default=0.0
		)

	def path_active_flow_count(self, path: Path, ignore_hosts=None) -> int:
		if not path:
			return 0
		ignore_hosts = ignore_hosts or set()
		path_edges = self.path_edges(path)
		path_switches = set(path)
		seen_flow_keys = set()
		count = 0
		with self.feedback_lock:
			active_items = list(self.active_weighted_paths.items())
		for (flow_key, src_host, dst_host), active_path in active_items:
			if flow_key in seen_flow_keys:
				continue
			seen_flow_keys.add(flow_key)
			if {src_host, dst_host} == set(ignore_hosts):
				continue
			if path_edges.intersection(self.path_edges(active_path)):
				count += 1
				continue
			if path_switches.intersection(active_path):
				count += 1
		return count

	def ensure_q_state(self, state: State, paths: List[Path]) -> None:
		if state not in self.q_table:
			self.q_table[state] = {path: 0.0 for path in paths}
			return

		for path in paths:
			if path not in self.q_table[state]:
				self.q_table[state][path] = 0.0

	def select_path(
		self,
		src_host: str,
		dst_host: str,
		src_switch: str,
		dst_switch: str,
	) -> Path:
		state = self.q_state(src_host, dst_host)
		paths = self.get_all_paths(src_switch, dst_switch)

		if not paths:
			self.logger.warning(
				"No simple paths available for %s -> %s (cutoff=%s)",
				src_switch,
				dst_switch,
				self.path_cutoff
			)
			return ()

		self.ensure_q_state(state, paths)
		self.epsilon = self.epsilon_schedule.get_epsilon(self.epsilon_step)
		self.epsilon_step += 1
		self.logger.info(
			"Selecting path for %s -> %s state=%s (paths=%s epsilon_step=%s epsilon=%s)",
			src_switch,
			dst_switch,
			state,
			len(paths),
			self.epsilon_step - 1,
			self.epsilon
		)

		warmup_path = self.warmup_path(state, paths)
		if warmup_path is not None:
			self.logger.info(
				"Warmup selected path (trials=%s/%s util=%.3f active=%s): %s",
				self.action_counts[state][warmup_path],
				self.warmup_trials_per_action,
				self.path_max_utilization(warmup_path),
				self.path_active_flow_count(warmup_path, set(state[:2])),
				" -> ".join(warmup_path)
			)
			return warmup_path

		if random.random() < self.epsilon:
			choice = random.choice(paths)
			self.logger.info(
				"Exploration selected path (util=%.3f active=%s): %s",
				self.path_max_utilization(choice),
				self.path_active_flow_count(choice, set(state[:2])),
				" -> ".join(choice)
			)
			return choice

		if self.action_selection == "ucb":
			choice, score = self.select_ucb_path(state, paths)
			penalty = self.active_path_overlap_penalty(choice, state)
			self.logger.info(
				"UCB selected path (score=%.3f state_trials=%s action_trials=%s q=%.3f overlap_penalty=%.3f util=%.3f active=%s): %s",
				score,
				self.state_counts[state],
				self.action_counts[state][choice],
				self.q_table[state].get(choice, 0.0),
				penalty,
				self.path_max_utilization(choice),
				self.path_active_flow_count(choice, set(state[:2])),
				" -> ".join(choice)
			)
			return choice

		path_scores = {
			path: self.q_table[state].get(path, 0.0)
			- self.active_path_overlap_penalty(path, state)
			- self.path_utilization_penalty * self.path_max_utilization(path)
			for path in paths
		}
		max_value = max(path_scores.values())
		best_paths = [
			path for path in paths
			if path_scores[path] == max_value
		]
		choice = random.choice(best_paths)
		self.logger.info(
			"Exploitation selected path (best_q=%.3f candidates=%s): %s",
			max_value,
			len(best_paths),
			" -> ".join(choice)
		)
		return choice

	def select_ucb_path(self, state: State, paths: List[Path]):
		total = max(self.state_counts[state], 1)
		best_score = None
		best_paths = []
		for path in paths:
			q_value = self.q_table[state].get(path, 0.0)
			trials = self.action_counts[state][path]
			bonus = self.ucb_c * math.sqrt(math.log(total + 1) / (trials + 1))
			penalty = self.active_path_overlap_penalty(path, state)
			util_penalty = self.path_utilization_penalty * self.path_max_utilization(path)
			score = q_value + bonus - penalty - util_penalty
			if best_score is None or score > best_score:
				best_score = score
				best_paths = [path]
			elif score == best_score:
				best_paths.append(path)
		return random.choice(best_paths), best_score

	def warmup_path(self, state: State, paths: List[Path]):
		if self.warmup_trials_per_action <= 0:
			return None
		warmup_state = self.reward_buffer_state(state)
		under_sampled = [
			path for path in paths
			if self.warmup_action_counts[warmup_state][path] < self.warmup_trials_per_action
		]
		if not under_sampled:
			return None
		return sorted(
			under_sampled,
			key=lambda path: (
				self.warmup_action_counts[warmup_state][path],
				self.path_max_utilization(path),
				self.path_active_flow_count(path, set(state[:2])),
				path,
			)
		)[0]

	def active_path_overlap_penalty(self, path: Path, state: State = None) -> float:
		if self.path_overlap_penalty <= 0 or not path:
			return 0.0

		path_edges = set(zip(path, path[1:]))
		path_edges |= {(dst, src) for src, dst in path_edges}
		path_switches = set(path)
		seen_flow_keys = set()
		overlap_units = 0

		with self.feedback_lock:
			active_items = list(self.active_weighted_paths.items())

		for (flow_key, src_host, dst_host), active_path in active_items:
			if flow_key in seen_flow_keys:
				continue
			seen_flow_keys.add(flow_key)
			if state is not None and {src_host, dst_host} == set(state):
				continue
			active_edges = set(zip(active_path, active_path[1:]))
			active_edges |= {(dst, src) for src, dst in active_edges}
			shared_edges = path_edges.intersection(active_edges)
			shared_switches = path_switches.intersection(active_path)
			overlap_units += (2 * len(shared_edges)) + len(shared_switches)

		return self.path_overlap_penalty * overlap_units

	def install_path(
		self,
		path: Path,
		src_host: str,
		dst_host: str,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	) -> bool:
		success = True

		for index, current_switch in enumerate(path):
			current_dpid = self.switch_to_dpid(current_switch)

			if current_dpid not in self.datapaths:
				self.logger.warning("Switch %s not connected yet", current_switch)
				success = False
				continue

			datapath = self.datapaths[current_dpid]
			parser = datapath.ofproto_parser

			if index == len(path) - 1:
				next_node = dst_host
			else:
				next_node = path[index + 1]

			try:
				out_port = self.get_out_port(current_switch, next_node)
			except KeyError:
				success = False
				continue

			actions = [parser.OFPActionOutput(out_port)]
			match = self.flow_match(
				parser,
				src_host,
				dst_host,
				protocol,
				src_port,
				dst_port
			)

			self.add_flow(
				datapath=datapath,
				priority=self.flow_rule_priority if protocol in ("tcp", "udp") else 100,
				match=match,
				actions=actions,
				idle_timeout=self.learned_flow_idle_timeout
			)

		return success

	def install_bidirectional_flow_path(
		self,
		path: Path,
		src_host: str,
		dst_host: str,
		protocol: str,
		src_port: int,
		dst_port: int,
	) -> bool:
		forward_ok = self.install_path(
			path,
			src_host,
			dst_host,
			protocol,
			src_port,
			dst_port
		)
		reverse_ok = self.install_path(
			tuple(reversed(path)),
			dst_host,
			src_host,
			protocol,
			dst_port,
			src_port
		)
		return forward_ok and reverse_ok

	def forward_current_packet(self, msg, path: Path, dst_host: str) -> bool:
		datapath = msg.datapath
		current_switch = self.dpid_to_switch(datapath.id)

		if current_switch not in path:
			self.logger.warning(
				"Current switch %s not in selected path %s",
				current_switch,
				path
			)
			return False

		index = path.index(current_switch)

		if index == len(path) - 1:
			next_node = dst_host
		else:
			next_node = path[index + 1]

		try:
			out_port = self.get_out_port(current_switch, next_node)
		except KeyError:
			return False

		self.packet_out(datapath, msg, out_port)
		return True

	def update_q_value(
		self,
		state: State,
		action: Path,
		reward: float,
		count_observation: bool = True,
	) -> None:
		state = self.normalize_state(state)
		src_switch, dst_switch = self.state_switches(state)
		paths = self.get_all_paths(src_switch, dst_switch)
		self.ensure_q_state(state, paths)
		target = self.reward_target(state, reward)
		current_q = self.q_table[state].get(action, 0.0)
		updated = current_q + self.alpha * (target - current_q)
		self.q_table[state][action] = updated
		if count_observation:
			self.state_counts[state] += 1
			self.action_counts[state][action] += 1
		self.logger.info(
			"Q-update state=%s action_len=%s reward=%.3f target=%.3f baseline=%.3f old_q=%.3f new_q=%.3f action_trials=%s",
			state,
			len(action),
			reward,
			target,
			self.reward_baselines.get(self.reward_baseline_state(state), 0.0),
			current_q,
			updated,
			self.action_counts[state][action]
		)

	def observe_action_reward(self, state: State, action: Path, reward: float) -> None:
		state = self.normalize_state(state)
		buffer_state = self.reward_buffer_state(state)
		self.state_counts[state] += 1
		self.action_counts[state][action] += 1
		self.warmup_action_counts[buffer_state][action] += 1
		key = (buffer_state, action)
		buffer = self.action_reward_buffers[key]
		buffer.append(reward)
		if len(buffer) < self.action_reward_window:
			self.logger.info(
				"Q-buffer state=%s action_len=%s reward=%.3f buffered=%s/%s",
				state,
				len(action),
				reward,
				len(buffer),
				self.action_reward_window
			)
			return
		smoothed_reward = sum(buffer) / len(buffer)
		self.action_reward_buffers[key] = []
		self.logger.info(
			"Q-smoothed reward state=%s action_len=%s reward=%.3f samples=%s",
			state,
			len(action),
			smoothed_reward,
			self.action_reward_window
		)
		self.update_q_value(
			state,
			action,
			smoothed_reward,
			count_observation=False
		)

	def reward_buffer_state(self, state: State) -> State:
		return tuple(state[:2])

	def reward_baseline_state(self, state: State) -> State:
		if self.reward_baseline_scope == "full":
			return tuple(state)
		if self.reward_baseline_scope == "pair":
			return self.reward_buffer_state(state)
		self.logger.warning(
			"Unknown REWARD_BASELINE_SCOPE=%s, falling back to pair",
			self.reward_baseline_scope
		)
		self.reward_baseline_scope = "pair"
		return self.reward_buffer_state(state)

	def normalize_state(self, state) -> State:
		return tuple(state)

	def reward_target(self, state: State, reward: float) -> float:
		if not self.use_advantage_reward:
			return reward

		baseline_state = self.reward_baseline_state(state)
		if baseline_state not in self.reward_baselines:
			self.reward_baselines[baseline_state] = reward
			if reward <= self.bad_reward_direct_threshold:
				return reward
			return 0.0

		baseline = self.reward_baselines[baseline_state]
		if reward <= self.bad_reward_direct_threshold:
			self.reward_baselines[baseline_state] = (
				(1.0 - self.reward_baseline_alpha) * baseline
				+ self.reward_baseline_alpha * reward
			)
			return reward

		target = (reward - baseline) * self.advantage_reward_scale
		target = max(-1.0, min(1.0, target))
		self.reward_baselines[baseline_state] = (
			(1.0 - self.reward_baseline_alpha) * baseline
			+ self.reward_baseline_alpha * reward
		)
		return target

	def start_feedback_listener(self) -> None:
		thread = threading.Thread(target=self.feedback_loop, daemon=True)
		thread.start()
		self.logger.info(
			"Weighted QoS feedback listener started on %s:%s profile=%s",
			self.feedback_host,
			self.feedback_port,
			self.weighted_profile
		)

	def feedback_loop(self) -> None:
		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		sock.bind((self.feedback_host, self.feedback_port))
		while True:
			payload, _addr = sock.recvfrom(65535)
			try:
				record = json.loads(payload.decode("utf-8"))
				self.handle_metric_feedback(record)
			except Exception as exc:
				self.logger.exception("Failed to process live QoS feedback: %s", exc)

	def feedback_key_from_record(self, record: Dict[str, object]):
		return (
			record.get("episode"),
			record.get("flow_id"),
		)

	def host_pair_key(self, src_host: str, dst_host: str):
		return tuple(sorted((src_host, dst_host)))

	def packet_flow_key(
		self,
		src_host: str,
		dst_host: str,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	):
		if not protocol:
			return None
		if dst_port is not None:
			key = self.flow_keys_by_packet.get(
				(src_host, dst_host, protocol, "dst", dst_port)
			)
			if key is not None:
				return key
		if src_port is not None:
			key = self.flow_keys_by_packet.get(
				(src_host, dst_host, protocol, "src", src_port)
			)
			if key is not None:
				return key
		return None

	def decision_feedback_key(
		self,
		src_host: str,
		dst_host: str,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	):
		key = self.packet_flow_key(
			src_host,
			dst_host,
			protocol,
			src_port,
			dst_port
		)
		if key is not None:
			return key
		candidates = self.pending_by_host_pair.get(
			self.host_pair_key(src_host, dst_host),
			set()
		)
		if len(candidates) == 1:
			return next(iter(candidates))
		if len(candidates) > 1:
			self.logger.info(
				"Ambiguous weighted feedback match for %s -> %s protocol=%s src_port=%s dst_port=%s candidates=%s",
				src_host,
				dst_host,
				protocol,
				src_port,
				dst_port,
				len(candidates)
			)
		return None

	def has_current_feedback_key(
		self,
		src_host: str,
		dst_host: str,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	) -> bool:
		return self.decision_feedback_key(
			src_host,
			dst_host,
			protocol,
			src_port,
			dst_port
		) is not None

	def handle_flow_start(self, record: Dict[str, object]) -> None:
		src_host = record.get("src")
		dst_host = record.get("dst")
		episode = record.get("episode")
		flow_id = record.get("flow_id")
		if not src_host or not dst_host or episode is None or not flow_id:
			return

		key = self.feedback_key_from_record(record)
		with self.feedback_lock:
			protocol = record.get("protocol")
			port = record.get("port")
			old_keys = {
				self.flow_keys_by_packet.get((src_host, dst_host, protocol, "dst", port)),
				self.flow_keys_by_packet.get((dst_host, src_host, protocol, "src", port)),
			}
			for old_key in old_keys:
				if old_key is None or old_key == key:
					continue
				self.pending_feedback.pop(old_key, None)
				for active_key in list(self.active_weighted_paths.keys()):
					if active_key[0] == old_key:
						self.active_weighted_paths.pop(active_key, None)
				self.clear_flow_key(old_key)
			self.flow_keys_by_packet[
				(src_host, dst_host, protocol, "dst", port)
			] = key
			self.flow_keys_by_packet[
				(dst_host, src_host, protocol, "src", port)
			] = key
		self.logger.info(
			"Flow feedback window started key=%s protocol=%s",
			key,
			record.get("protocol")
		)
		self.invalidate_measured_flow(
			src_host,
			dst_host,
			key,
			record.get("protocol"),
			record.get("port")
		)

	def clear_flow_key(self, flow_key) -> None:
		for packet_key, mapped_flow_key in list(self.flow_keys_by_packet.items()):
			if mapped_flow_key == flow_key:
				self.flow_keys_by_packet.pop(packet_key, None)

	def remember_pending_feedback(
		self,
		src_host: str,
		dst_host: str,
		update: Dict[str, object],
		path: Path,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	) -> bool:
		with self.feedback_lock:
			key = self.decision_feedback_key(
				src_host,
				dst_host,
				protocol,
				src_port,
				dst_port
			)
			if key is None:
				return False
			for pending_update in self.pending_feedback.get(key, []):
				if (
					pending_update.get("state") == update.get("state")
					and pending_update.get("action") == update.get("action")
					and pending_update.get("reverse_state") == update.get("reverse_state")
					and pending_update.get("reverse_action") == update.get("reverse_action")
				):
					return False
			self.pending_feedback[key].append(update)
			self.pending_by_host_pair[self.host_pair_key(src_host, dst_host)].add(key)
			exact_key = self.packet_flow_key(
				src_host,
				dst_host,
				protocol,
				src_port,
				dst_port
			)
			if key == exact_key:
				self.active_weighted_paths[(key, src_host, dst_host)] = tuple(path)
				self.active_weighted_paths[(key, dst_host, src_host)] = tuple(reversed(path))
			return True

	def get_active_weighted_path(
		self,
		src_host: str,
		dst_host: str,
		protocol: str = None,
		src_port: int = None,
		dst_port: int = None,
	) -> Path:
		with self.feedback_lock:
			key = self.packet_flow_key(
				src_host,
				dst_host,
				protocol,
				src_port,
				dst_port
			)
			return self.active_weighted_paths.get(
				(key, src_host, dst_host),
				()
			)

	def handle_metric_feedback(self, record: Dict[str, object]) -> None:
		if record.get("event") == "flow_start":
			self.handle_flow_start(record)
			return

		src_host = record.get("src")
		dst_host = record.get("dst")
		protocol = record.get("protocol")
		episode = record.get("episode")
		flow_id = record.get("flow_id")
		if not src_host or not dst_host or not protocol or episode is None or not flow_id:
			return

		feedback_key = self.feedback_key_from_record(record)
		with self.feedback_lock:
			metrics = self.feedback_metrics.setdefault(feedback_key, {})
			if protocol == "tcp" and record.get("throughput_mbps") is not None:
				metrics["throughput_gbps"] = float(record["throughput_mbps"]) / 1000.0
			if protocol == "udp":
				if record.get("jitter_ms") is not None:
					metrics["jitter_ms"] = float(record["jitter_ms"])
				if record.get("loss_pct") is not None:
					metrics["plr_pct"] = float(record["loss_pct"])
			if record.get("rtt_ms") is not None:
				metrics["rtt_ms"] = float(record["rtt_ms"])

			if "tcp" in self.expected_protocols and "throughput_gbps" not in metrics:
				return
			if "udp" in self.expected_protocols and (
				"jitter_ms" not in metrics or "plr_pct" not in metrics
			):
				return

			metrics.setdefault("throughput_gbps", 0.0)
			metrics.setdefault("rtt_ms", 0.0)
			metrics.setdefault("jitter_ms", 0.0)
			metrics.setdefault("plr_pct", 0.0)
			reward = self.qos_reward.compute_reward(metrics)
			self.recent_pair_metrics[
				self.host_pair_key(src_host, dst_host)
			] = dict(metrics)

			pending = self.pending_feedback.get(feedback_key, [])
			if not pending:
				self.logger.info(
					"Live QoS reward %.3f for key=%s has no pending decision",
					reward,
					feedback_key
				)
				return

			updates = list(pending)
			self.pending_feedback.pop(feedback_key, None)
			self.pending_by_host_pair[
				self.host_pair_key(src_host, dst_host)
			].discard(feedback_key)
			if not self.pending_by_host_pair[self.host_pair_key(src_host, dst_host)]:
				self.pending_by_host_pair.pop(self.host_pair_key(src_host, dst_host), None)
			for active_key in list(self.active_weighted_paths.keys()):
				if active_key[0] == feedback_key:
					self.active_weighted_paths.pop(active_key, None)
			self.clear_flow_key(feedback_key)

		for update in updates:
			self.observe_action_reward(update["state"], update["action"], reward)
			if "reverse_state" in update and "reverse_action" in update:
				self.observe_action_reward(update["reverse_state"], update["reverse_action"], reward)
		self.logger.info(
			"Live weighted QoS reward for %s -> %s: %.3f metrics=%s updates=%s util=%.3f active=%s",
			src_host,
			dst_host,
			reward,
			metrics,
			len(updates),
			self.path_max_utilization(updates[0]["action"]) if updates else 0.0,
			self.path_active_flow_count(updates[0]["action"], {src_host, dst_host}) if updates else 0
		)

	def resolve_arp_destination(self, arp_pkt):
		if arp_pkt is None:
			return None

		return self.ip_to_host.get(arp_pkt.dst_ip)

	def forward_ignored_packet(self, msg, src_host: str, dst_host: str) -> None:
		src_switch = self.host_to_switch[src_host]
		dst_switch = self.host_to_switch[dst_host]
		try:
			path = nx.shortest_path(
				self.graph,
				source=src_switch,
				target=dst_switch,
				weight="weight"
			)
		except (nx.NetworkXNoPath, nx.NodeNotFound):
			self.logger.warning(
				"No shortest path for ignored packet %s -> %s",
				src_host,
				dst_host
			)
			return
		self.forward_current_packet(msg, tuple(path), dst_host)

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

		is_arp = eth.ethertype == ether_types.ETH_TYPE_ARP
		if is_arp:
			arp_pkt = pkt.get_protocol(arp.arp)

			if src_host is None:
				self.logger.info("Unknown ARP source MAC: %s", src_mac)
				return

			dst_host = self.resolve_arp_destination(arp_pkt)
			if dst_host is None:
				self.logger.info("Unknown ARP destination IP: %s", arp_pkt.dst_ip)
				return

			self.logger.info(
				"Handling ARP packet: %s -> %s",
				src_host,
				dst_host
			)

		if src_host is None or dst_host is None:
			self.logger.info(
				"Unknown unicast mapping: src_mac=%s dst_mac=%s",
				src_mac,
				dst_mac
			)
			return

		src_switch = self.host_to_switch[src_host]
		dst_switch = self.host_to_switch[dst_host]

		traffic_protocol = self.packet_protocol(pkt)
		traffic_src_port, traffic_dst_port = self.packet_transport_ports(pkt)
		measured = (
			traffic_protocol in ("tcp", "udp")
			and self.packet_flow_key(
				src_host,
				dst_host,
				traffic_protocol,
				traffic_src_port,
				traffic_dst_port
			) is not None
		)

		ipv4_pkt = pkt.get_protocol(ipv4.ipv4)
		if is_arp or traffic_protocol not in ("tcp", "udp") or (
			self.reward_mode == "weighted" and not measured
		):
			self.logger.debug(
				"Ignoring side packet for R-Learner reward_mode=%s src=%s dst=%s eth_type=%s ip_proto=%s",
				self.reward_mode,
				src_host,
				dst_host,
				eth.ethertype,
				getattr(ipv4_pkt, "proto", None)
			)
			self.forward_ignored_packet(msg, src_host, dst_host)
			return

		active_path = ()
		if self.reward_mode == "weighted":
			active_path = self.get_active_weighted_path(
				src_host,
				dst_host,
				traffic_protocol,
				traffic_src_port,
				traffic_dst_port
			)

		selected_path = active_path if active_path else self.select_path(
			src_host,
			dst_host,
			src_switch,
			dst_switch
		)
		if not selected_path:
			self.logger.warning(
				"No available path for %s -> %s",
				src_host,
				dst_host
			)
			reward = compute_controller_reward(False, (), self.reward_mode)
			if self.reward_mode != "weighted":
				self.update_q_value(
					self.q_state(src_host, dst_host),
					(),
					reward
				)
			return

		self.logger.info(
			"R-Learner selected path %s -> %s: %s",
			src_host,
			dst_host,
			" -> ".join(selected_path)
		)

		if measured:
			install_success = self.install_bidirectional_flow_path(
				selected_path,
				src_host,
				dst_host,
				traffic_protocol,
				traffic_src_port,
				traffic_dst_port
			)
		else:
			install_success = self.install_path(selected_path, src_host, dst_host)
		forward_success = self.forward_current_packet(msg, selected_path, dst_host)
		success = install_success and forward_success

		if self.reward_mode == "weighted":
			if not success:
				self.logger.warning(
					"Skipping weighted Q update without live QoS feedback for %s -> %s (install=%s forward=%s)",
					src_host,
					dst_host,
					install_success,
					forward_success
				)
				return
			if active_path:
				return
			if not self.has_current_feedback_key(
				src_host,
				dst_host,
				traffic_protocol,
				traffic_src_port,
				traffic_dst_port
			):
				self.logger.debug(
					"Skipping weighted Q queue for %s -> %s because no measured flow window is active",
					src_host,
					dst_host
				)
				return
			queued = self.remember_pending_feedback(src_host, dst_host, {
				"state": self.q_state(src_host, dst_host),
				"action": selected_path,
				"reverse_state": self.q_state(dst_host, src_host),
				"reverse_action": tuple(reversed(selected_path)),
			}, selected_path, traffic_protocol, traffic_src_port, traffic_dst_port)
			if queued:
				self.logger.info(
					"R-Learner queued weighted Q feedback for %s -> %s path=%s (install=%s forward=%s)",
					src_host,
					dst_host,
					" -> ".join(selected_path),
					install_success,
					forward_success
				)
			return

		reward = compute_controller_reward(success, selected_path, self.reward_mode)
		self.logger.info(
			"Reward for %s -> %s: %s (install=%s forward=%s)",
			src_host,
			dst_host,
			reward,
			install_success,
			forward_success
		)
		self.update_q_value(
			self.q_state(src_host, dst_host),
			selected_path,
			reward
		)

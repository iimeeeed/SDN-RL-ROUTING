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
import socket
import threading
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
State = Tuple[str, str, str, str]


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

		self.mac_to_host = {
			mac: host for host, mac in self.host_to_mac.items()
		}
		self.ip_to_host = {
			ip: host for host, ip in self.host_to_ip.items()
		}

		self.alpha = 0.1
		self.epsilon_schedule = build_epsilon_schedule()
		self.epsilon_step = 0
		self.epsilon = self.epsilon_schedule.get_epsilon(self.epsilon_step)
		self.path_cutoff = int(os.environ.get("PATH_CUTOFF", "6"))
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

		self.q_table: Dict[State, Dict[Path, float]] = {}
		self.path_cache: Dict[State, List[Path]] = {}
		self.qos_reward = QoSReward(
			weights=WEIGHT_CONFIGS[self.weighted_profile],
			window=self.baseline_window,
			rtt_clip_ms=self.qos_rtt_clip_ms
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
			"R-Learner config: alpha=%s epsilon_schedule=%s epsilon=%s path_cutoff=%s learned_flow_idle_timeout=%s reward_mode=%s weighted_profile=%s qos_rtt_clip_ms=%s",
			self.alpha,
			self.epsilon_schedule,
			self.epsilon,
			self.path_cutoff,
			self.learned_flow_idle_timeout,
			self.reward_mode,
			self.weighted_profile,
			self.qos_rtt_clip_ms
		)
		if self.reward_mode == "weighted":
			self.start_feedback_listener()

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
			paths = [
				tuple(path)
				for path in nx.all_simple_paths(
					self.graph,
					source=src_switch,
					target=dst_switch,
					cutoff=self.path_cutoff
				)
			]

		self.path_cache[state] = paths
		return paths

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
		state = (src_host, dst_host, src_switch, dst_switch)
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
			"Selecting path for %s -> %s (paths=%s epsilon_step=%s epsilon=%s)",
			src_switch,
			dst_switch,
			len(paths),
			self.epsilon_step - 1,
			self.epsilon
		)

		if random.random() < self.epsilon:
			choice = random.choice(paths)
			self.logger.info(
				"Exploration selected path: %s",
				" -> ".join(choice)
			)
			return choice

		max_value = max(self.q_table[state].get(path, 0.0) for path in paths)
		best_paths = [
			path for path in paths
			if self.q_table[state].get(path, 0.0) == max_value
		]
		choice = random.choice(best_paths)
		self.logger.info(
			"Exploitation selected path (best_q=%.3f candidates=%s): %s",
			max_value,
			len(best_paths),
			" -> ".join(choice)
		)
		return choice

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

	def update_q_value(self, state: State, action: Path, reward: float) -> None:
		src_host, dst_host, _src_switch, dst_switch = state
		if not action or len(action) < 2:
			paths = self.get_all_paths(state[2], state[3])
			self.ensure_q_state(state, paths)
			current_q = self.q_table[state].get(action, 0.0)
			updated = current_q + self.alpha * (reward - current_q)
			self.q_table[state][action] = updated
			self.logger.info(
				"Q-update state=%s action_len=%s reward=%s old_q=%.3f new_q=%.3f",
				state,
				len(action),
				reward,
				current_q,
				updated
			)
			return

		for index in range(len(action) - 2, -1, -1):
			current_switch = action[index]
			next_switch = action[index + 1]
			step_state = (src_host, dst_host, current_switch, dst_switch)
			step_action = tuple(action[index:])
			paths = self.get_all_paths(current_switch, dst_switch)
			self.ensure_q_state(step_state, paths)

			if next_switch == dst_switch:
				target = reward
			else:
				next_state = (src_host, dst_host, next_switch, dst_switch)
				next_paths = self.get_all_paths(next_switch, dst_switch)
				self.ensure_q_state(next_state, next_paths)
				next_max = max(self.q_table[next_state].values()) if self.q_table[next_state] else 0.0
				target = self.gamma * next_max

			current_q = self.q_table[step_state].get(step_action, 0.0)
			updated = current_q + self.alpha * (target - current_q)
			self.q_table[step_state][step_action] = updated
			self.logger.info(
				"Q-update state=%s action_len=%s reward=%s target=%.3f old_q=%.3f new_q=%.3f",
				step_state,
				len(step_action),
				reward,
				target,
				current_q,
				updated
			)

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
			if self.pending_feedback.get(key):
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

			pending = self.pending_feedback.get(feedback_key, [])
			if not pending:
				self.logger.info(
					"Live QoS reward %.3f for key=%s has no pending decision",
					reward,
					feedback_key
				)
				return

			update = pending.pop(0)
			if not pending:
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

		self.update_q_value(update["state"], update["action"], reward)
		if "reverse_state" in update and "reverse_action" in update:
			self.update_q_value(update["reverse_state"], update["reverse_action"], reward)
		self.logger.info(
			"Live weighted QoS reward for %s -> %s: %.3f metrics=%s update=q",
			src_host,
			dst_host,
			reward,
			metrics
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
					(src_host, dst_host, src_switch, dst_switch),
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
				"state": (src_host, dst_host, src_switch, dst_switch),
				"action": selected_path,
				"reverse_state": (dst_host, src_host, dst_switch, src_switch),
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
			(src_host, dst_host, src_switch, dst_switch),
			selected_path,
			reward
		)

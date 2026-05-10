#!/usr/bin/env python3

"""
Staged dual-agent controller: R-Learner exploration then R-Optimizer refinement.

Usage:
	TOPOLOGY=abilene EXPLORE_EPISODES=50 ryu-manager controllers/staged_controller.py
	TOPOLOGY=fat_tree EXPLORE_EPISODES=50 ryu-manager controllers/staged_controller.py
"""

import os
import sys

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

from exploration.fixed_epsilon import get_epsilon
from reward.binary_reward import compute_reward


Path = Tuple[str, ...]
State = Tuple[str, str]
PolicyState = Tuple[str, str, str]


class StagedController(app_manager.RyuApp):
	OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

	def __init__(self, *args, **kwargs):
		super(StagedController, self).__init__(*args, **kwargs)

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
		self.gamma = 0.95
		self.epsilon = get_epsilon(episode=0)
		self.path_cutoff = int(os.environ.get("PATH_CUTOFF", "6"))
		self.baseline_window = int(os.environ.get("BASELINE_WINDOW", "20"))
		self.policy_bias = float(os.environ.get("POLICY_BIAS", "0.7"))

		self.q_table: Dict[State, Dict[Path, float]] = {}
		self.path_cache: Dict[State, List[Path]] = {}

		self.policy: Dict[PolicyState, Dict[str, float]] = {}
		self.reward_history: List[float] = []

		self.phase = "explore"
		self.episode = 0
		self.explore_episodes = int(os.environ.get("EXPLORE_EPISODES", "50"))

		self.logger.info("Loaded topology: %s", topology_name)
		self.logger.info("Switches: %s", self.topology.SWITCHES)
		self.logger.info("Hosts: %s", list(self.host_to_switch.keys()))
		self.logger.info(
			"Staged config: explore_episodes=%s alpha=%s gamma=%s epsilon=%s path_cutoff=%s",
			self.explore_episodes,
			self.alpha,
			self.gamma,
			self.epsilon,
			self.path_cutoff
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

	def select_path(self, src_switch, dst_switch) -> Path:
		state = (src_switch, dst_switch)
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

		if random.random() < self.epsilon:
			choice = random.choice(paths)
			self.logger.info("Exploration selected path: %s", " -> ".join(choice))
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

	def update_q_value(self, state: State, action: Path, reward: float) -> None:
		paths = self.get_all_paths(state[0], state[1])
		self.ensure_q_state(state, paths)

		current_q = self.q_table[state].get(action, 0.0)
		max_next = max(self.q_table[state].values()) if self.q_table[state] else 0.0

		updated = current_q + self.alpha * (reward + self.gamma * max_next - current_q)
		self.q_table[state][action] = updated
		self.logger.info(
			"Q-update state=%s action_len=%s reward=%s old_q=%.3f new_q=%.3f",
			state,
			len(action),
			reward,
			current_q,
			updated
		)

	def get_neighbors(self, switch_name, dst_switch, visited: List[str]) -> List[str]:
		candidates = []
		for neighbor in self.graph.neighbors(switch_name):
			if neighbor in visited:
				continue
			if nx.has_path(self.graph, neighbor, dst_switch):
				candidates.append(neighbor)
		return candidates

	def initialize_policy(self, state: PolicyState, actions: List[str]) -> None:
		if not actions:
			return

		if state in self.policy:
			existing_actions = set(self.policy[state].keys())
			if set(actions) == existing_actions:
				return

		raw = {action: random.random() + 0.01 for action in actions}
		total = sum(raw.values())
		self.policy[state] = {action: value / total for action, value in raw.items()}

	def sample_action(self, state: PolicyState, actions: List[str]) -> str:
		self.initialize_policy(state, actions)
		weights = [self.policy[state][action] for action in actions]
		return random.choices(actions, weights=weights, k=1)[0]

	def normalize_policy(self, state: PolicyState) -> None:
		values = self.policy[state]
		for action in list(values.keys()):
			if values[action] < 0.0:
				values[action] = 0.0

		total = sum(values.values())
		if total <= 0.0:
			uniform = 1.0 / len(values)
			self.policy[state] = {action: uniform for action in values}
			return

		self.policy[state] = {action: value / total for action, value in values.items()}

	def update_policy(self, state: PolicyState, action: str, reward: float, baseline: float) -> None:
		if state not in self.policy:
			return

		advantage = reward - baseline
		actions = list(self.policy[state].keys())

		for act in actions:
			if act == action:
				self.policy[state][act] += self.alpha * advantage * (1 - self.policy[state][act])
			else:
				self.policy[state][act] -= self.alpha * advantage * self.policy[state][act]

		self.normalize_policy(state)
		self.logger.info(
			"Policy update state=%s action=%s reward=%s baseline=%.3f",
			state,
			action,
			reward,
			baseline
		)

	def compute_baseline(self) -> float:
		if not self.reward_history:
			return 0.0

		window = self.reward_history[-self.baseline_window:]
		return sum(window) / len(window)

	def initialize_optimizer_from_q_table(self) -> None:
		seeded_states = 0
		for (src_switch, dst_switch), path_scores in self.q_table.items():
			if not path_scores:
				continue

			best_path = max(path_scores.items(), key=lambda item: item[1])[0]
			if not best_path or len(best_path) < 2:
				continue

			for index in range(len(best_path) - 1):
				current = best_path[index]
				next_hop = best_path[index + 1]
				visited = list(best_path[: index + 1])
				actions = self.get_neighbors(current, dst_switch, visited)
				if not actions:
					actions = list(self.graph.neighbors(current))

				if not actions:
					continue

				state = (src_switch, dst_switch, current)
				if next_hop in actions:
					if len(actions) == 1:
						self.policy[state] = {next_hop: 1.0}
					else:
						remainder = max(1.0 - self.policy_bias, 0.0)
						per_other = remainder / (len(actions) - 1)
						self.policy[state] = {
							action: (self.policy_bias if action == next_hop else per_other)
							for action in actions
						}
				else:
					uniform = 1.0 / len(actions)
					self.policy[state] = {action: uniform for action in actions}

				seeded_states += 1

		self.logger.info(
			"Initialized optimizer from Q-table: seeded_states=%s bias=%.2f",
			seeded_states,
			self.policy_bias
		)

	def build_path(self, src_switch: str, dst_switch: str) -> Tuple[List[str], List[Tuple[PolicyState, str]]]:
		if src_switch == dst_switch:
			return [src_switch], []

		path = [src_switch]
		state_actions: List[Tuple[PolicyState, str]] = []

		while path[-1] != dst_switch and len(path) <= self.path_cutoff:
			current = path[-1]
			visited = path
			actions = self.get_neighbors(current, dst_switch, visited)

			if not actions:
				self.logger.warning(
					"No next-hop actions from %s toward %s (visited=%s)",
					current,
					dst_switch,
					visited
				)
				return [], state_actions

			state = (src_switch, dst_switch, current)
			action = self.sample_action(state, actions)
			state_actions.append((state, action))
			path.append(action)

		if path[-1] != dst_switch:
			self.logger.warning(
				"Path cutoff reached without reaching %s (path=%s)",
				dst_switch,
				path
			)
			return [], state_actions

		return path, state_actions

	def install_path(self, path: List[str], dst_host: str) -> bool:
		dst_mac = self.host_to_mac[dst_host]
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
			match = parser.OFPMatch(eth_dst=dst_mac)

			self.add_flow(
				datapath=datapath,
				priority=100,
				match=match,
				actions=actions,
				idle_timeout=0
			)

		return success

	def forward_current_packet(self, msg, path: List[str], dst_host: str) -> bool:
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

	def resolve_arp_destination(self, arp_pkt):
		if arp_pkt is None:
			return None

		return self.ip_to_host.get(arp_pkt.dst_ip)

	def handle_rlearner(self, msg, src_host: str, dst_host: str) -> None:
		src_switch = self.host_to_switch[src_host]
		dst_switch = self.host_to_switch[dst_host]

		selected_path = self.select_path(src_switch, dst_switch)
		if not selected_path:
			self.logger.warning("No available path for %s -> %s", src_host, dst_host)
			reward = compute_reward(False)
			self.update_q_value((src_switch, dst_switch), (), reward)
			return

		self.logger.info(
			"R-Learner selected path %s -> %s: %s",
			src_host,
			dst_host,
			" -> ".join(selected_path)
		)

		install_success = self.install_path(list(selected_path), dst_host)
		forward_success = self.forward_current_packet(msg, list(selected_path), dst_host)
		reward = compute_reward(install_success and forward_success)
		self.logger.info(
			"Reward for %s -> %s: %s (install=%s forward=%s)",
			src_host,
			dst_host,
			reward,
			install_success,
			forward_success
		)
		self.update_q_value((src_switch, dst_switch), selected_path, reward)

	def handle_roptimizer(self, msg, src_host: str, dst_host: str) -> None:
		src_switch = self.host_to_switch[src_host]
		dst_switch = self.host_to_switch[dst_host]

		path, state_actions = self.build_path(src_switch, dst_switch)
		if not path:
			self.logger.warning("No available path for %s -> %s", src_host, dst_host)
			reward = compute_reward(False)
		else:
			self.logger.info(
				"R-Optimizer selected path %s -> %s: %s",
				src_host,
				dst_host,
				" -> ".join(path)
			)
			install_success = self.install_path(path, dst_host)
			forward_success = self.forward_current_packet(msg, path, dst_host)
			reward = compute_reward(install_success and forward_success)
			self.logger.info(
				"Reward for %s -> %s: %s (install=%s forward=%s)",
				src_host,
				dst_host,
				reward,
				install_success,
				forward_success
			)

		self.reward_history.append(reward)
		baseline = self.compute_baseline()
		self.logger.info(
			"Baseline=%.3f reward_history=%s updates=%s",
			baseline,
			len(self.reward_history),
			len(state_actions)
		)

		for state, action in state_actions:
			self.update_policy(state, action, reward, baseline)

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

		if src_host is None or dst_host is None:
			self.logger.info(
				"Unknown unicast mapping: src_mac=%s dst_mac=%s",
				src_mac,
				dst_mac
			)
			return

		self.logger.info(
			"Phase=%s episode=%s/%s handling %s -> %s",
			self.phase,
			self.episode,
			self.explore_episodes,
			src_host,
			dst_host
		)

		if self.phase == "explore":
			self.handle_rlearner(msg, src_host, dst_host)
			self.episode += 1
			if self.episode >= self.explore_episodes:
				self.phase = "optimize"
				self.reward_history = []
				self.initialize_optimizer_from_q_table()
				self.logger.info(
					"Switching to R-Optimizer phase at episode=%s",
					self.episode
				)
			return

		self.handle_roptimizer(msg, src_host, dst_host)

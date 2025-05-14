"""
rfsa_optimizer.py
~~~~~~~~~~~~~~~~~
RFSA 启发式 Optimizer：把新 Pending Pod 放入现有节点，
放不下就开新节点（最小可行规格、最低成本、区域均衡）。
"""

from __future__ import annotations
from typing import List, Tuple, Dict
import json
import random
from pathlib import Path

from optimizer_interface import BaseOptimizer
from resource_types import Pod, Node
from resource_model import ResourceModel
from data_loader import load_price_map, load_spec_map

# —— 工具函数沿用之前的解析 —— #
def _parse_cpu(cpu_str: str) -> float:
    return float(cpu_str[:-1]) / 1000 if cpu_str.endswith("m") else float(cpu_str)


def _parse_mem(mem_str: str) -> float:
    if mem_str.endswith("Gi"):
        return float(mem_str[:-2])
    if mem_str.endswith("Mi"):
        return float(mem_str[:-2]) / 1024
    if mem_str.endswith("Ki"):
        return float(mem_str[:-2]) / (1024 ** 2)
    return float(mem_str)

MAX_WORKER_NODES = 6        # 不包括 master
MAX_CLUSTER_CPU  = 30       # worker 集群 vCPU 上限

# ———————————————————————————————————————————————— #
# Optimizer 实现
# ———————————————————————————————————————————————— #
class RFSAOptimizer(BaseOptimizer):
    """
    参数
    ----
    price_json : str
        区域机型 → on-demand 单价。
    spec_json  : str
        区域机型 → (vCPU, Gi) 规格。
    """

    def __init__(self,
                 price_json: str = "region_machine_prices.json",
                 spec_json: str = "machine_types.json",
                 suit_thresh: float = 0.6):
        super().__init__()
        self.price_map = load_price_map(price_json)
        self.spec_map = load_spec_map(spec_json)
        self.node_info_path = Path(__file__).parent / "node_info.json"
        self.suit_thresh = suit_thresh

    # ————————————————————————————————————————————— #
    # 核心接口
    # ————————————————————————————————————————————— #
    def optimize(self,
                 current: ResourceModel,
                 pending: List[Pod],
                 mode="incremental") -> Tuple[ResourceModel, List[Pod]]:
        """
        把 pending Pod 安排进 plan（现有节点优先），必要时开新节点。
        """
        plan = current.clone()
        still_pending: List[Pod] = []

        # 将 Pending Pod 按 (cpu+mem) 总量降序，先放难塞的
        pending.sort(key=lambda p: (p.cpu + p.mem), reverse=True)

        for pod in pending:
            # 1⃣ 先尝试所有现有节点
            chosen = self._fit_existing(plan, pod)

            # 2⃣ 放不下 → 新开节点
            if not chosen:
                new_node = self._open_new_node(plan, pod)
                if new_node:
                    new_node.add_pod(pod)
                    plan.open_node(new_node)
                    plan.pod2node[pod.full_name] = new_node.name
                    chosen = True

            if not chosen:
                # 仍无法安置
                still_pending.append(pod)

        return plan, still_pending

    # ————————————————————————————————————————————— #
    # 辅助：放入现有节点
    # ————————————————————————————————————————————— #
    def _fit_existing(self, plan: ResourceModel, pod: Pod) -> bool:
        best = None             # (cpu_left, suit, node)

        for nd in plan.nodes.values():
            if not nd.can_fit(pod) or nd.name=="master":
                continue

            # 插入后 CPU / MEM 剩余比例
            cpu_left = nd.usable_cpu_cap - (nd.cpu_used)
            mem_left = nd.mem_cap - (nd.mem_used)

            # ① 主要指标：CPU 剩余百分比，越小越好
            cpu_ratio = (cpu_left-pod.cpu) / nd.cpu_cap

            # ② 次指标：原 Suitability，用来打破平分
            rho_pod = pod.cpu / pod.mem if pod.mem else float("inf")
            rho_node = cpu_left / mem_left if mem_left else float("inf")
            suit = abs(rho_pod - rho_node) / (rho_node + 1e-6)

            # 组合得分：CPU 优先
            # 组装多元键：cpu_left 是主键，suit 次键
            key = (cpu_ratio, suit)

            if best is None or key < best[:2]:
                best = (cpu_left, suit, nd)

        if best:
            nd = best[2]
            nd.add_pod(pod)  # 资源 & pods 列表同步更新
            plan.pod2node[pod.full_name] = nd.name
            return True
        return False

    # ————————————————————————————————————————————— #
    # 辅助：为 Pod 选择最小成本节点
    # ————————————————————————————————————————————— #
    def _open_new_node(self, plan: ResourceModel, pod: Pod) -> Node | None:
        need_cpu, need_mem = pod.cpu, pod.mem
        rho_pod = need_cpu / need_mem if need_mem else float("inf")
        CPU_WEIGHT = 0.5
        suit_thresh = 0.6  # 可作为 self.suit_thresh 注入

        good: list[tuple[float, float, str, str]] = []  # (suit, price, region, mt)
        other: list[tuple[float, float, str, str]] = []  # (suit, price, region, mt)

        # —— 当前 worker 数 & CPU 总量（排除 master）——
        worker_nodes = [nd for nd in plan.nodes.values() if nd.name != "master"]
        curr_count = len(worker_nodes)
        curr_cpu_cap = sum(nd.cpu_cap for nd in worker_nodes)

        # 若节点数已到上限，直接返回 None
        if curr_count >= MAX_WORKER_NODES:
            return None

        # ——— 枚举全部满足规格的机型 ———
        for region, mts in self.spec_map.items():
            for mt, (vcpu, mem) in mts.items():
                if vcpu - Node.DEFAULT_OVERHEAD_CPU  < need_cpu or mem < need_mem:
                    continue
                if vcpu < need_cpu or mem < need_mem:
                    continue

                if curr_cpu_cap + vcpu > MAX_CLUSTER_CPU:
                    continue
                price = (
                    self.price_map.get(region, {})
                    .get("OnDemand", {})  # ← 先取价目表类别
                    .get(mt, 1e9)  # ← 再取机型价格
                )
                if price <= 0 or price ==1e9:
                    continue

                rho_node = vcpu / mem if mem else float("inf")
                suit = abs(rho_pod - rho_node) / (rho_node + 1e-6)
                cpu_left = (vcpu - Node.DEFAULT_OVERHEAD_CPU ) - need_cpu
                cpu_ratio = cpu_left / vcpu  # 越小越好

                tup = (cpu_left,suit, price, region, mt)
                if suit <= suit_thresh:
                    good.append(tup)
                else:
                    other.append(tup)

        # ——— 没有任何候选 ———
        if not good and not other:
            return None

        # ——— 选择最终候选列表 ———
        cand = good if good else other  # 优先 Suit ≤ 阈值
        # 1) Suit 升序 → 2) 价格升序 → 3) 区域负载升序
        cand.sort(key=lambda t: (t[0], t[1],t[2], len(plan.nodes_by_region().get(t[3], []))))

        cpu_waste, suit, price, region, mt = cand[0]
        cpu_cap, mem_cap = self.spec_map[region][mt]
        node_name = f"rfsa-{region}-{mt}-{random.randint(10000, 99999)}"
        return Node(node_name, region, mt, cpu_cap, mem_cap, price)

"""
二维 Best-Fit Decreasing (CPU, MEM) 生成初始部署方案。
"""
from typing import Dict, List

from .model import PodRequest, NodeInfo, MachineType
from .pricing_loader import PricingLoader
from .constants import MAX_WORKER_NODES, MAX_TOTAL_VCPU


class BFDInitialPlacer:
    """
    输入一批待调度 Pod，给出 (nodes, placements) 初始解：
        - nodes: List[NodeInfo]  (包含现有 + 新建)
        - placements: Dict[pod_id -> NodeInfo]
    """

    def __init__(self, cluster_nodes: List[NodeInfo]):
        self.cluster_nodes = cluster_nodes          # 现有节点
        self.pricing = PricingLoader()

    # ------------------------------ public API ------------------------------ #
    def place(self, pods: List[PodRequest]):
        # 1. 将 Pod 按 CPU 再按 MEM 降序排列
        pending = sorted(pods, key=lambda p: (p.cpu, p.mem), reverse=True)
        placements: Dict[str, NodeInfo] = {}
        nodes = self.cluster_nodes.copy()

        for pod in pending:
            target = self._best_fit(nodes, pod)
            if not target:
                # 需要新建节点 -> 以同一区域最低价机型为准
                target = self._create_new_node(pod)
                nodes.append(target)
            # 放置
            target.used_cpu += pod.cpu
            target.used_mem += pod.mem
            placements[pod.id] = target

        return nodes, placements

    # ----------------------------- helpers ---------------------------------- #
    @staticmethod
    def _fits(node: NodeInfo, pod: PodRequest) -> bool:
        return node.free_cpu >= pod.cpu and node.free_mem >= pod.mem

    def _best_fit(self, nodes: List[NodeInfo], pod: PodRequest):
        """
        选择能放下 Pod 的节点中，剩余 CPU 最少者（CPU 优先）；
        若 CPU 相同，选择剩余 MEM 最少者。
        """
        feasible = [n for n in nodes if self._fits(n, pod)]
        if not feasible:
            return None
        feasible.sort(key=lambda n: (n.free_cpu - pod.cpu,
                                     n.free_mem - pod.mem))
        return feasible[0]

    def _create_new_node(self, pod: PodRequest) -> NodeInfo:
        """
        在所有区域中选择能容纳 Pod 的最低价机型，并生成 NodeInfo
        （仅作为“虚拟”节点用来打包，真正创建由调度器完成）。
        """
        best_mt: MachineType | None = None
        for region, mts in self.pricing._machines.items():
            for mt in mts:
                if mt.vcpus >= pod.cpu and mt.mem_gib >= pod.mem:
                    if (best_mt is None) or (mt.price < best_mt.price):
                        best_mt = mt
        if not best_mt:
            raise RuntimeError(f"无法找到可容纳 {pod.name} 的机型")
        return NodeInfo(name=f"virtual-{best_mt.name}-{pod.name}",
                        machine=best_mt, used_cpu=0, used_mem=0)

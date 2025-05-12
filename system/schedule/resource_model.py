"""
resource_model.py
~~~~~~~~~~~~~~~~~
在内存中维护『节点 ←→ Pod』映射，用于启发式与后续优化。
"""
from __future__ import annotations
from typing import Dict, List, Set

from resource_types import Pod, Node


class ResourceModel:
    """
    保存 **单个调度方案** 的完整快照；
    支持克隆、移动 Pod、开关节点等基本操作。
    """
    def __init__(self,
                 nodes: Dict[str, Node],
                 pod2node: Dict[str, str]):
        self.nodes: Dict[str, Node] = nodes
        self.pod2node: Dict[str, str] = pod2node

    # —— 克隆 —— #
    def clone(self) -> "ResourceModel":
        # 深度拷贝：Node 对象 & 字典结构
        import copy
        return copy.deepcopy(self)

    # —— 查询辅助 —— #
    def nodes_by_region(self) -> Dict[str, List[str]]:
        reg2: Dict[str, List[str]] = {}
        for nd in self.nodes.values():
            reg2.setdefault(nd.region, []).append(nd.name)
        return reg2

    def pods_on_node(self, node_name: str) -> List[str]:
        return [p for p, n in self.pod2node.items() if n == node_name]

    def all_pods(self) -> Set[str]:
        return set(self.pod2node.keys())

    # —— 原子操作 —— #
    def move_pod(self, pod: Pod, src: Node, dst: Node):
        src.rm_pod(pod)
        dst.add_pod(pod)
        self.pod2node[pod.full_name] = dst.name

    def open_node(self, node: Node):
        self.nodes[node.name] = node

    def close_node(self, node: Node):
        assert node.cpu_used == 0 and node.mem_used == 0, "node not empty"
        del self.nodes[node.name]

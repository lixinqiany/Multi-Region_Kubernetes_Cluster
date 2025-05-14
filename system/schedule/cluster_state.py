"""
cluster_state.py
~~~~~~~~~~~~~~~~
负责把 ClusterMonitor 提供的实时信息转换成 ResourceModel，
供调度 / 优化算法使用。算法模块**只依赖 ResourceModel**，
不直接访问 K8s API。
"""
from __future__ import annotations
from typing import Dict
from pathlib import Path
import json
from ClusterMonitor import ClusterMonitor          # 用户已有
from resource_types import Pod, Node
from resource_model import ResourceModel

# JSON 存储节点到 (machine_type, region) 的映射
NODE_INFO_PATH = Path(__file__).parent / "node_info.json"


def _load_node_info() -> Dict[str, Dict[str, str]]:
    """读取 node_info.json，返回 {node_name: {machine_type, region}}"""
    if not NODE_INFO_PATH.exists():
        return {}
    return json.loads(NODE_INFO_PATH.read_text(encoding="utf-8"))


def _save_node_info(info: Dict[str, Dict[str, str]]):
    """持久化更新 node_info.json"""
    NODE_INFO_PATH.write_text(json.dumps(info, indent=2), encoding="utf-8")


# —— 基础解析 —— #
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


# —— 公开主函数 —— #
def snapshot_cluster(monitor: ClusterMonitor) -> ResourceModel:
    """
    采集 **所有 Running / Pending Pod** + **所有节点**，
    返回一个 ResourceModel（节点-Pod 映射），供算法/调度器使用。
    """
    # 1. Nodes
    node_info = _load_node_info()
    nodes: Dict[str, Node] = {}
    for n in monitor.core_v1.list_node().items:
        name   = n.metadata.name
        conds = {c.type: c.status for c in n.status.conditions}
        if conds.get("Ready") != "True":
            continue

        meta = node_info.get(name)
        if not meta:
            # 没有映射：跳过，不做假设
            continue

        mtype = meta["machine_type"]
        region = meta["region"]
        alloc_cpu = float(n.status.allocatable["cpu"])
        alloc_mem = float(n.status.allocatable["memory"][:-2]) / 1024 /1024
        # ⚠ 价格信息不在 K8s，算法里再注入；先用 0
        nodes[name] = Node(name, region, mtype, alloc_cpu, alloc_mem, price=0.0,is_existing=True)

    # 2. Pods
    pods = []
    pod2node: Dict[str, str] = {}
    resp = monitor.core_v1.list_pod_for_all_namespaces()
    for p in resp.items:
        if p.status.phase not in ("Running", "Pending"):
            continue
        # 改动在这里：先初始化然后遍历容器，保护 requests=None
        cpu_req = 0.0
        mem_req = 0.0
        for c in p.spec.containers:
            reqs = c.resources.requests or {}  # 关键：若 None 则用 {}
            req_cpu = _parse_cpu(c.resources.requests.get("cpu", "0")) if c.resources.requests else 0
            lim_cpu = _parse_cpu(c.resources.limits.get("cpu", "0")) if c.resources.limits else 0
            cpu_req += max(req_cpu, lim_cpu)
            req_mem = _parse_mem(c.resources.requests.get("memory", "0")) if c.resources.requests else 0
            lim_mem = _parse_mem(c.resources.limits.get("memory", "0")) if c.resources.limits else 0
            mem_req += max(req_mem, lim_mem)
            #mem_req += _parse_mem(reqs.get("memory", "0"))

        pod = Pod(p.metadata.name,
                  p.metadata.namespace,
                  cpu_req,
                  mem_req,
                  p.metadata.labels or {})
        pod.is_new = False
        if p.status.phase == "Running":
            node_name = p.spec.node_name
            if node_name in nodes:
                record_flag = p.metadata.namespace == "default"
                try:
                    nodes[node_name].add_pod(pod, record=record_flag)
                except AssertionError as e:
                    if str(e) == "resource overflow":
                        # 容忍溢出：忽略该 Pod 对建模的影响
                        #   （可选）累加到一个 overflow 计数，用于日志
                        continue
                    else:
                        raise
                pod2node[pod.full_name] = node_name
                pods.append(pod)
        else:
            # Pending 先放置为 “未绑定”
            pass

    return ResourceModel(nodes, pod2node)

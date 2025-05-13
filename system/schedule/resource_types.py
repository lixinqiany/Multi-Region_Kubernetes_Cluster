"""
resource_types.py
~~~~~~~~~~~~~~~~~
轻量级 Pod / Node 抽象，只保留调度所需字段。
"""
from __future__ import annotations
from typing import Dict


class Pod:
    """Kubernetes Pod 的极简描述（仅 requests 维度）"""
    __slots__ = ("name", "namespace", "cpu", "mem", "labels", "is_new")

    def __init__(self,
                 name: str,
                 namespace: str,
                 cpu: float,
                 mem: float,
                 labels: Dict[str, str] | None = None,
                 ):
        self.name = name
        self.namespace = namespace
        self.cpu = cpu          # 请求 CPU，单位：核
        self.mem = mem          # 请求内存，单位：Gi
        self.labels = labels or {}
        self.is_new = False

    # —— 方便打印 / 去重 —— #
    @property
    def full_name(self) -> str:
        return f"{self.namespace}/{self.name}"

    def __hash__(self):           # 允许放入 set / dict
        return hash(self.full_name)

    def __repr__(self):
        return (f"Pod({self.full_name}, "
                f"cpu={self.cpu}, mem={self.mem}, "
                f"labels={self.labels})")


class Node:
    DEFAULT_OVERHEAD_CPU  = 0.15  # ✨ 系统保留 vCPU
    SPECIAL_NODE_NAME = "node-1"
    SPECIAL_OVERHEAD_CPU = 0.40
    """云节点描述：规格 + 成本 + 当前已用资源"""
    __slots__ = ("name", "region", "machine_type",
                 "cpu_cap", "mem_cap",
                 "cpu_used", "mem_used",
                 "price","_pods","is_existing","usable_cpu_cap","overhead_cpu")

    def __init__(self,
                 name: str,
                 region: str,
                 machine_type: str,
                 cpu_cap: float,
                 mem_cap: float,
                 price: float,is_existing: bool = False):
        self.name = name
        self.region = region
        self.machine_type = machine_type
        self.cpu_cap = cpu_cap
        self.mem_cap = mem_cap
        self.cpu_used = 0.0
        self.mem_used = 0.0
        self.price = price
        self.overhead_cpu = (
            Node.SPECIAL_OVERHEAD_CPU
            if name == Node.SPECIAL_NODE_NAME
            else Node.DEFAULT_OVERHEAD_CPU
        )
        self._pods: list[Pod] = []  # ← 初始化为空列表
        self.is_existing = is_existing  # ← 新增
        if is_existing:
            self.overhead_cpu = 0.0
        self.usable_cpu_cap = max(0.0, cpu_cap - self.overhead_cpu)

    @property
    def pods(self) -> list[Pod]:
        """返回当前节点上已记录的 Pod 对象列表（只读）。"""
        return self._pods

    # —— 资源判定 —— #
    def can_fit(self, pod: "Pod") -> bool:
        return (
            self.cpu_used + pod.cpu <= self.usable_cpu_cap and
            self.mem_used + pod.mem <= self.mem_cap
        )

    def add_pod(self, pod: Pod, record: bool = True):
        assert self.can_fit(pod), "resource overflow"
        self.cpu_used += pod.cpu
        self.mem_used += pod.mem
        if record:
            # —— 先去重 —— #
            self._pods = [p for p in self._pods
                          if p.full_name != pod.full_name]

            self._pods.append(pod)

    def rm_pod(self, pod: Pod):
        self.cpu_used -= pod.cpu
        self.mem_used -= pod.mem
        # 可能是算法中的临时 Pod 对象（== 比较不同），按 full_name 匹配删除
        self._pods = [p for p in self._pods if p.full_name != pod.full_name]

    # —— 利用率 —— #
    @property
    def cpu_idle_ratio(self) -> float:
        return (self.cpu_cap - self.cpu_used) / self.cpu_cap

    @property
    def mem_idle_ratio(self) -> float:
        return (self.mem_cap - self.mem_used) / self.mem_cap

    @property
    def util_ratio(self) -> float:
        if self.usable_cpu_cap == 0:
            return 1.0
        return self.cpu_used / self.usable_cpu_cap

        # ------------------------------------------------
        def clone(self):
            dup = Node(self.name, self.region, self.machine_type,
                       self.cpu_cap, self.mem_cap, self.price, self.is_existing)
            dup.overhead_cpu = self.overhead_cpu
            dup.usable_cpu_cap = self.usable_cpu_cap
            dup.cpu_used = self.cpu_used
            dup.mem_used = self.mem_used
            dup._pods = [p.clone() for p in self._pods]  # 假设 Pod 定义有 clone
            return dup

    def __repr__(self):
        return (f"Node({self.name}, {self.region}/{self.machine_type}, "
                f"cpu={self.cpu_used:.1f}/{self.cpu_cap}, "
                f"mem={self.mem_used:.1f}/{self.mem_cap}, "
                f"price=${self.price}/h)")

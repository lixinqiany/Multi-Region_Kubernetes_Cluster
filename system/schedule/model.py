"""
通用数据结构：Node、MachineType、PodRequest
"""
from dataclasses import dataclass
from typing import Set


@dataclass
class MachineType:
    name: str
    vcpus: int
    mem_gib: float
    price: float            # USD / hour
    region: str


@dataclass
class NodeInfo:
    name: str
    machine: MachineType
    used_cpu: float
    used_mem: float

    @property
    def free_cpu(self) -> float:
        return self.machine.vcpus - self.used_cpu

    @property
    def free_mem(self) -> float:
        return self.machine.mem_gib - self.used_mem


@dataclass(frozen=True)
class PodRequest:
    name: str
    cpu: float             # 核
    mem: float             # GiB

    @property
    def id(self):
        return self.name

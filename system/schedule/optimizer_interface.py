"""
optimizer_interface.py
~~~~~~~~~~~~~~~~~~~~~~
调度 / 优化算法的抽象基类。
后续 RFSA + 模拟退火请继承并实现 `optimize()` 方法。
"""

from abc import ABC, abstractmethod
from typing import List, Tuple

from resource_model import ResourceModel
from resource_types import Pod


class BaseOptimizer(ABC):
    """
    任何调度算法都必须：
      • 接受 【当前资源模型】+ 【待调度 Pending Pod 列表】
      • 输出 一个 **新的 ResourceModel**（可能与输入相同）
    """

    def __init__(self):
        super().__init__()

    @abstractmethod
    @abstractmethod
    def optimize(self,
                 plan: ResourceModel,
                 pending: list[Pod],
                 mode: str = "incremental") -> tuple[ResourceModel, list[Pod]]:
        """
        Parameters
        ----------
        current : ResourceModel
            现有节点 / Pod 占用快照。
        pending : List[Pod]
            尚未被调度、需要放置的 Pod 列表
            （调度器会从 K8s Pending 队列中传入）。

        Returns
        -------
        new_plan : ResourceModel
            经过算法优化后的部署方案。
        still_pending : List[Pod]
            若有极端 Pod 仍无法安置，返回它们；调度器可记录告警。
        """
        raise NotImplementedError


class NoOpOptimizer(BaseOptimizer):
    def optimize(self, current, pending,mode: str = "incremental"):
        # 什么都不做：直接原样返回
        return current, pending
"""
scheduler.py
~~~~~~~~~~~~
调度主模块：定期循环
  • 采集集群状态 → ResourceModel
  • 调用 Optimizer 生成新方案
  • Diff 现状 ←→ 新方案，决定扩容 / 缩容 / Pod 迁移
目前仅实现 **骨架**：
  – 节点扩缩容：调用 VMManager.create_node / delete_node
  – Pod 迁移：留 TODO（需结合业务，或采用 K8s Default Descheduler）
"""

from __future__ import annotations
import time, os, json
import logging
from typing import List, Dict
from pathlib import Path
from sa_optimizer import SAOptimizer
from contourpy.types import point_dtype

from ClusterMonitor import ClusterMonitor
from VMManager import VMManager

from cluster_state import snapshot_cluster
from optimizer_interface import BaseOptimizer, NoOpOptimizer
from resource_model import ResourceModel
from resource_types import Pod
from rfsa_optimizer import RFSAOptimizer


class Scheduler:
    """
    Parameters
    ----------
    optimizer : BaseOptimizer
        调度算法实例（默认 NoOp，占位）。
    interval_sec : int
        主循环间隔（秒）。
    """

    def __init__(self,
                 optimizer: BaseOptimizer | None = None,
                 interval_sec: int = 120):
        self.monitor = ClusterMonitor()
        self.vm = VMManager()
        self.optimizer = optimizer or NoOpOptimizer()
        self.interval_sec = interval_sec
        self.logger = logging.getLogger("SAScheduler")
        self.logger.setLevel(logging.INFO)

    # ──────────────────────────────────────────────
    # 主循环
    # ──────────────────────────────────────────────
    def run_forever(self):
        """
        while-true 调度循环；Ctrl+C 可退出。
        """
        self.logger.info("SA Scheduler started. interval=%ss", self.interval_sec)
        while True:
            start = time.time()
            try:
                self._run_once()
            except Exception as exc:     # noqa
                self.logger.exception("scheduler run_once failed: %s", exc)

            # 维持固定间隔
            elapsed = time.time() - start
            time.sleep(max(0, self.interval_sec - elapsed))

    # ──────────────────────────────────────────────
    # 单轮调度
    # ──────────────────────────────────────────────
    def _run_once(self):
        # 1. 采集状态
        cur_plan = snapshot_cluster(self.monitor)
        pending_pods = self._fetch_pending_list()

        # 2. 算法优化
        new_plan, still_pending = self.optimizer.optimize(cur_plan, pending_pods)

        # 3. 执行 plan 差异
        self._apply_plan(cur_plan, new_plan)

        if still_pending:
            self.logger.warning("Unscheduled pod(s): %s",
                                [p.full_name for p in still_pending])
        self.logger.info("schedule cycle finished")

    # ──────────────────────────────────────────────
    # 帮助：拉取 Pending Pod 列表
    # ──────────────────────────────────────────────
    def _fetch_pending_list(self) -> List[Pod]:
        plist = []
        resp = self.monitor.core_v1.list_pod_for_all_namespaces(
            field_selector="status.phase=Pending")
        for p in resp.items:
            # 只处理 custom-scheduling、default、未绑定的 Pod
            if p.spec.scheduler_name != "custom-scheduling":
                continue
            if p.metadata.namespace != "default":
                continue
            if p.spec.node_name:
                continue

            pod=p
            # CPU / MEM 解析逻辑与 cluster_state 一致
            from cluster_state import _parse_cpu, _parse_mem
            # 计算请求
            cpu_req = 0.0
            mem_req = 0.0
            for c in p.spec.containers:
                reqs = c.resources.requests or {}
                cpu_req += _parse_cpu(reqs.get("cpu", "0"))
                mem_req += _parse_mem(reqs.get("memory", "0"))

            plist.append(
                Pod(p.metadata.name,
                    p.metadata.namespace,
                    cpu_req,
                    mem_req,
                    p.metadata.labels or {})
            )
        return plist

    # ──────────────────────────────────────────────
    # 扩容 / 缩容 / Pod 迁移
    # ──────────────────────────────────────────────
    def _apply_plan(self,
                    old: ResourceModel,
                    new: ResourceModel):
        """
        Diff 两份 ResourceModel：
          • 节点差异 → VMManager 扩缩容
          • Pod 绑定差异 → TODO：驱逐 + rebind
        """
        NODE_INFO_PATH = Path(__file__).parent / "node_info.json"
        node_info = json.loads(NODE_INFO_PATH.read_text(encoding="utf-8"))
        old_nodes = set(old.nodes.keys())
        new_nodes = set(new.nodes.keys())

        # ——— 删除节点 ———
        for node in old_nodes - new_nodes:
            region_guess = "-".join(node.split("-")[:-1])
            self.logger.info("delete node %s", node)
            self.vm.delete_node(node, region_guess)
            node_info.pop(node, None)

        # ——— 创建新节点 ———
        for node in new_nodes - old_nodes:
            nd = new.nodes[node]
            self.logger.info("create node %s (%s/%s)",
                             node, nd.region, nd.machine_type)
            self.vm.create_node(node, nd.region, nd.machine_type)
            node_info[node] = {
                "machine_type": nd.machine_type,
                "region": nd.region
            }

        Path(NODE_INFO_PATH).write_text(json.dumps(node_info, indent=2),
                                        encoding="utf-8")

        # ——— Pod 迁移 / 绑定 ———
        for pod_full, tgt_node in new.pod2node.items():
            if pod_full in old.pod2node and old.pod2node[pod_full] == tgt_node:
                # 位置未变
                continue
            # TODO: 这里可使用 eviction + sched.bind 或手动 patch nodeName
            self.logger.debug("Need move %s -> %s", pod_full, tgt_node)

        # 小结
        self.logger.info("apply diff done: +%d nodes, -%d nodes",
                         len(new_nodes - old_nodes),
                         len(old_nodes - new_nodes))


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./config/single-cloud-ylxq-ed1608c43bb4.json"
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from sa_optimizer import SAOptimizer
    optimizer = RFSAOptimizer(
        price_json="data/gcp/region_machine_prices.json",
        spec_json="data/gcp/machine_types.json"
    )
    sa = SAOptimizer(seed_optimizer=optimizer,
                     n_iter=200,  # 每个温度邻域尝试次数
                     T0=60, Tmin=1, alpha=0.9)

    sched = Scheduler(optimizer=sa, interval_sec=120)   # 每 2 分钟调度一次
    sched.run_forever()

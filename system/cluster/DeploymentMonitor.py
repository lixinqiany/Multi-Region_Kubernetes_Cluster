# cluster/deployment_monitor.py

"""
DeploymentMonitor
=================

监控 Deployment 资源：
  - 每个采样周期记录所有 Deployment 的期望副本数、可用副本数、就绪副本数。
  - 记录新增或删除的 Deployment 事件到 history 文件。
输出：
  - data/deployment/<deployment-name>-status.csv
    header: timestamp,desired_replicas,available_replicas,ready_replicas
  - data/deployment/deployment-history.csv
    header: timestamp,action,deployment
"""

import os, time, logging
from .ClusterMonitor import ClusterMonitor


class DeploymentMonitor:
    """监控 Kubernetes Deployment 的状态和变更。"""

    def __init__(self, interval: int, namespace: str = "default"):
        """
        :param interval: 采样间隔（秒）
        :param namespace: 目标命名空间
        """
        self.cluster = ClusterMonitor()
        self.interval = interval
        self.namespace = namespace
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        # 保存上一次看到的 Deployment 名称集合
        self.prev_deploys= set()

    def _write_csv(self, path: str, header: str, row: list[str]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a") as f:
            if new:
                f.write(header + "\n")
            f.write(",".join(row) + "\n")

    def run(self):
        """循环监控 Deployment，记录状态与新增/删除事件。"""
        status_header = "timestamp,desired_replicas,available_replicas,ready_replicas"
        history_header = "timestamp,action,deployment"
        self.logger.info("deployment资源监控器 DeploymentMonitor started.")
        while True:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")

            # 获取当前 Deployment 列表及其状态
            deploys = self.cluster.list_deployments(self.namespace)
            curr_names = set(deploys.keys())

            # 1) 记录新增/删除 Deployment 到 history
            added = curr_names - self.prev_deploys
            removed = self.prev_deploys - curr_names
            hist_path = "data/deployment/deployment-history.csv"
            for name in added:
                self._write_csv(hist_path, history_header, [ts, "ADD", name])
                self.logger.info(f"Deployment ADD {name}")
            for name in removed:
                self._write_csv(hist_path, history_header, [ts, "DEL", name])
                self.logger.info(f"Deployment DEL {name}")

            # 2) 对每个 Deployment 记录状态
            for name, sts in deploys.items():
                desired = sts.get("desired", 0)
                available = sts.get("available", 0)
                ready = sts.get("ready", 0)
                row = [ts, str(desired), str(available), str(ready)]
                path = f"data/deployment/{name}-status.csv"
                self._write_csv(path, status_header, row)
                self.logger.debug(
                    f"[{name}] desired={desired}, available={available}, ready={ready}"
                )

            # 3) 更新 prev_deploys
            self.prev_deploys = curr_names
            time.sleep(self.interval)

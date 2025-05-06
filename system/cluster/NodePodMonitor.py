import os, time, logging
from prometheus_api_client import PrometheusConnect
from .ClusterMonitor import ClusterMonitor


class NodePodMonitor:
    """节点Pod分布监控：记录各节点上Pod列表的变化历史。"""
    def __init__(self, prom_url, interval, namespace="default"):
        self.prom = PrometheusConnect(url=prom_url,
                                      disable_ssl=True)
        self.interval = interval
        self.namespace = namespace
        self.cluster = ClusterMonitor()

        self.prev_distribution = {}  # 上一次采样的节点->Pod集合 映射

        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.logger.info(f"pod分布监控器 连接prometheus服务器{self.prom.check_prometheus_connection()}")

    def write_csv(self, filepath, header, line_data):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        file_exists = os.path.isfile(filepath)
        with open(filepath, 'a') as f:
            if not file_exists:
                f.write(header + "\n")
            f.write(",".join(line_data) + "\n")

    def run(self):
        """启动节点Pod分布监控循环，检测Pod增减变化并记录。"""
        self.logger.info("Pod分布监控器 NodePodMonitor started.")
        header = "timestamp,action,pod"
        while True:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            # 构建当前分布：node -> set(Pod名称) via kubernets API
            current_dist = self.cluster.get_pod_node_map(self.namespace)
            # 若第一次运行，记录初始状态(将所有Pod视为新增)
            if not self.prev_distribution:
                self.prev_distribution = {node: pods.copy() for node, pods in current_dist.items()}
                # 记录初始存在的Pod作为添加事件
                for node, pods in current_dist.items():
                    filename = f"data/plan/{node}-pod-history.csv"
                    for pod in pods:
                        line = [timestamp, "ADD", pod]
                        self.write_csv(filename, header, line)
                        self.logger.info(f"[{node}] ADD {pod}")
            else:
                # 比较 current_dist 和 prev_distribution
                for node, pods in current_dist.items():
                    prev_pods = self.prev_distribution.get(node, set())
                    # 检查新增的Pods
                    added = pods - prev_pods
                    removed = prev_pods - pods
                    # 检查移除的Pods
                    path = f"data/plan/{node}-pod-history.csv"
                    for pod in added:
                        self.write_csv(path, header, [timestamp, "ADD", pod])
                        self.logger.info(f"[{node}] ADD {pod}")
                    for pod in removed:
                        self.write_csv(path, header, [timestamp, "DEL", pod])
                        self.logger.info(f"[{node}] DEL {pod}")

                # 检查之前存在但当前缺失的节点（该节点上所有Pod都被移除）
                for node, prev_pods in self.prev_distribution.items():
                    if node not in current_dist:
                        # 之前有Pod的节点现在无数据，表示该节点上Pod全无
                        for pod in prev_pods:
                            filename = f"data/plan/{node}-pod-history.csv"
                            line = [timestamp, "DEL", pod]
                            self.write_csv(filename, header, line)
                            self.logger.info(f"[{node}] DEL {pod}")
                # 更新 prev_distribution
                self.prev_distribution = {node: pods.copy() for node, pods in current_dist.items()}
            time.sleep(self.interval)

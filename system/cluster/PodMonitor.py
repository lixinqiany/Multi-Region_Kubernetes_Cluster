"""
pod utilization

cpu: sum(rate(container_cpu_usage_seconds_total{namespace="default", pod!="", container!="POD"}[1m])) by (pod)
- container_cpu_usage_seconds_total 是容器累计CPU使用时间。对默认命名空间 (namespace="default")下各Pod，过滤掉容器名为 "POD" 的基础容器.
- rate(...[1m]) 计算1分钟区间内CPU使用增长速率（即近似CPU核心使用量）。
- sum by(pod) 聚合容器得到Pod级别的每秒CPU使用量(单位：核)。

sum(kube_pod_container_resource_requests_cpu_cores{namespace="default"}) by (pod)
- 这给出每个Pod的总请求CPU（核）。
- CPU利用率 = (CPU使用量 / CPU请求)*100%。
- 例如某Pod请求1核，实际用0.25核，则利用率25%。在PromQL中可直接计算百分比，但在代码中我们获取两者再计算。

memory: sum(container_memory_usage_bytes{namespace="default", pod!="", container!="POD"}) by (pod)
- 汇总Pod中所有容器的内存用量(字节)。此值可能包含缓存，当无内存限制时甚至可能超过请求值。

sum(kube_pod_container_resource_requests_memory_bytes{namespace="default"}) by (pod)
- 计算 Pod 总请求内存。然后内存利用率 = (内存使用量 / 内存请求)*100%。若某Pod请求内存512Mi(≈536870912字节)，实际使用268435456字节，则利用率50%。
"""

import os, time, requests, logging
from prometheus_api_client import  PrometheusConnect
from .ClusterMonitor import ClusterMonitor

class PodMonitor:
    """Pod监控：采集默认命名空间下每个Pod的CPU/内存使用量和利用率。"""
    def __init__(self, prom_url, interval):
        self.prom = PrometheusConnect(url=prom_url,
                                      disable_ssl=True)
        self.interval = interval
        # ClusterMonitor用于获取Running Pods
        self.cluster = ClusterMonitor()
        # CPU使用量: 每Pod每秒使用的CPU核心数
        self.cpu_usage_query = (
            'sum('
            'node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate'
            '{namespace="default", container!=""}'
            ') by (pod)'
        )
        # CPU请求: 每Pod请求的CPU总核数
        self.cpu_request_query = (
            'sum(kube_pod_container_resource_requests{namespace="default", resource="cpu"}) by (pod)'
        )
        # 内存使用量: 每Pod当前使用内存字节数
        self.mem_usage_query = (
            'sum('
            'container_memory_working_set_bytes'
            '{job="kubelet", metrics_path="/metrics/cadvisor", cluster="", '
            'namespace="default", container!="" , image!=""}'
            ') by (pod)'
        )
        # 内存请求: 每Pod请求的内存总字节数
        self.mem_request_query = (
            'sum(kube_pod_container_resource_requests{namespace="default", resource="memory"}) by (pod)'
        )
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.logger.info(f"pod监控器 连接prometheus服务器{self.prom.check_prometheus_connection()}")

    def query(self, query):
        """与NodeMonitor.query_prometheus类似，发送查询并返回结果列表。"""
        try:
            return self.prom.custom_query(query=query)
        except Exception as e:
            self.logger.error(f"错误发生在像Prometheus发送query时\nPrometheus query error: {e}")
            return []

    def get_task_name(self, pod_name):
        """根据Pod名称推断任务名(Deployment前缀)。"""
        # 简单规则：去掉末尾由 '-' 分隔的随机字符串部分
        parts = pod_name.rsplit('-', 2)  # 以最后2个 '-' 为界拆分
        if len(parts) >= 2:
            return parts[0]
        return pod_name  # 若不符合约定命名则返回原名

    def write_csv(self, filepath, header, line_data):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        file_exists = os.path.isfile(filepath)
        with open(filepath, 'a') as f:
            if not file_exists:
                f.write(header + "\n")
            f.write(",".join(line_data) + "\n")

    def run(self):
        """启动Pod监控循环，定期查询所有Pod CPU/内存用量和利用率。"""
        self.logger.info("PodMonitor started.")
        header = "timestamp,cpu_usage(core),cpu_util(%),memory_usage(byte),memory_util(%)"
        while True:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            # 获取当前running的pods
            running_pods = self.cluster.get_running_pods(namespace="default")
            if not running_pods:
                self.logger.warning(f"目前没有正在运行的pods在default空间下")
                time.sleep(self.interval)
                continue
            # 查询四项数据
            cpu_use = {
                d["metric"]["pod"]: float(d["value"][1])
                for d in self.query(self.cpu_usage_query)
                if d["metric"]["pod"] in running_pods
            }
            cpu_req = {
                d["metric"]["pod"]: float(d["value"][1])
                for d in self.query(self.cpu_request_query)
                if d["metric"]["pod"] in running_pods
            }
            mem_use = {
                d["metric"]["pod"]: float(d["value"][1])
                for d in self.query(self.mem_usage_query)
                if d["metric"]["pod"] in running_pods
            }
            mem_req = {
                d["metric"]["pod"]: float(d["value"][1])
                for d in self.query(self.mem_request_query)
                if d["metric"]["pod"] in running_pods
            }
            for pod, cpu_val in cpu_use.items():
                cpu_request = cpu_req.get(pod, 0.0)
                cpu_pct = (cpu_val / cpu_request * 100) if cpu_request else 0.0

                mem_val = mem_use.get(pod, 0.0)
                mem_request = mem_req.get(pod, 0.0)
                mem_pct = (mem_val / mem_request * 100) if mem_request else 0.0

                row = [
                    timestamp,
                    f"{cpu_val:.3f}",
                    f"{cpu_pct:.2f}",
                    str(int(mem_val)),
                    f"{mem_pct:.2f}",
                ]
                task = self.get_task_name(pod)
                path = f"data/pod/{task}/{pod}-utilization.csv"
                self.write_csv(path, header, row)

                self.logger.debug(
                    f"{pod}: CPU {cpu_val:.3f}/{cpu_request} "
                    f"({cpu_pct:.1f}%), MEM {mem_val}/{mem_request} "
                    f"({mem_pct:.1f}%)"
                )
            time.sleep(self.interval)


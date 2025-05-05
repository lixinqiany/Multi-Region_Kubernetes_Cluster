"""
node utilization

cpu: (1 - avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[1m]))) * 100
- rate(node_cpu_seconds_total{mode="idle"}[1m]) 计算每个CPU核在1分钟区间的空闲时间占用率（秒/秒）。
- avg by(instance)(...): group by node name

memory: (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
- node_memory_MemAvailable_bytes 是节点可用内存（包括空闲和文件缓存等可回收内存）
- node_memory_MemTotal_bytes 是内存总量。

"""

import os, time, requests, logging
from prometheus_api_client import PrometheusConnect

class NodeMonitor:
    """节点监控：采集每个节点的CPU和内存利用率，并输出CSV。"""
    def __init__(self, prom_url, interval):
        self.prom = PrometheusConnect(url=prom_url,
                                      disable_ssl=True)
        self.interval = interval              # 采样间隔（秒）
        # 准备 PromQL 查询语句
        self.cpu_query = '(1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) by (instance)) * 100'
        self.mem_query = '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'

        # CPU 使用量 (cores)
        self.cpu_usage_q = 'sum(rate(node_cpu_seconds_total{mode!="idle"}[1m])) by (instance)'
        # CPU 总量 (cores)
        self.cpu_capacity_q = 'count(node_cpu_seconds_total{mode="idle"}) by (instance)'
        # 内存使用量 (bytes)
        self.mem_usage_q = 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes'
        # 内存总量 (bytes)
        self.mem_total_q = 'node_memory_MemTotal_bytes'
        # Logger设置
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.logger.info(f"连接prometheus服务器{self.prom.check_prometheus_connection()}")

    def query(self, query):
        """向 Prometheus 发送即时查询，返回结果中的value列表。"""
        try:
            return self.prom.custom_query(query=query)
        except Exception as e:
            self.logger.error(f"错误发生在像Prometheus发送query时\nPrometheus query error: {e}")
            return []

    def write_csv(self, filepath, header, line_data):
        """将行数据追加写入CSV文件; 若文件不存在则创建并写入表头。"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        file_exists = os.path.isfile(filepath)
        with open(filepath, 'a') as f:
            # 文件新建时写入表头
            if not file_exists:
                f.write(header + "\n")
            # 写入CSV行数据
            f.write(",".join(line_data) + "\n")

    def run(self):
        """启动监控循环，定期查询节点CPU和内存利用率并写入文件。"""
        self.logger.info("节点性能监控器 NodeMonitor started.")
        # 表头定义
        header = "timestamp,cpu_usage(core),cpu_capacity(core),cpu_util_percent,memory_usage_bytes,memory_total_bytes,memory_util_percent"
        while True:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")  # 当前时间
            # 查询 CPU 和 内存 利用率
            cpu_results = self.query(self.cpu_query)
            mem_results = self.query(self.mem_query)
            # 将结果整理为 节点:值 字典，方便匹配
            cpu_util_map = {}
            for item in cpu_results:
                node = item['metric'].get('instance', '<unknown>')
                value = item['value'][1]  # [ timestamp, value ]
                cpu_util_map[node] = value
            mem_util_map = {}
            for item in mem_results:
                node = item['metric'].get('instance', '<unknown>')
                value = item['value'][1]
                mem_util_map[node] = value
            usage_cpu = {d["metric"]["instance"]: float(d["value"][1]) for d in self.query(self.cpu_usage_q)}
            cap_cpu = {d["metric"]["instance"]: float(d["value"][1]) for d in self.query(self.cpu_capacity_q)}
            usage_mem = {d["metric"]["instance"]: float(d["value"][1]) for d in self.query(self.mem_usage_q)}
            total_mem = {d["metric"]["instance"]: float(d["value"][1]) for d in self.query(self.mem_total_q)}
            # 遍历所有节点的数据并写入各自文件
            for node, cpu_val in cpu_util_map.items():
                # 若内存结果中没有该节点，跳过或设为0
                cpu_use = usage_cpu.get(node, 0.0)
                cpu_cap = cap_cpu.get(node, 0.0)
                cpu_pct = cpu_util_map.get(node, 0.0)
                mem_use = usage_mem.get(node, 0.0)
                mem_tot = total_mem.get(node, 0.0)
                mem_pct = mem_util_map.get(node, 0.0)

                # 组织CSV行数据（利用率保留1位小数）
                line = [timestamp,
                        f"{cpu_use:.3f}",
                        f"{cpu_cap:.1f}",
                        f"{float(cpu_pct):.1f}",
                        str(int(mem_use)),
                        str(int(mem_tot)),
                        f"{float(mem_pct):.1f}"]
                filename = f"data/node/{node}-utilization.csv"
                self.write_csv(filename, header, line)
                self.logger.debug(
                    f"[{node}] CPU usage={cpu_use} cores, capacity={cpu_cap} cores, util={cpu_pct}%; "
                    f"MEM usage={mem_use} bytes, total={mem_tot} bytes, util={mem_pct}%"
                )
            # 等待下一个采样周期
            time.sleep(self.interval)

if __name__ == "__main__":
    monitor = NodeMonitor(prom_url="http://34.129.107.238:32501",
                          interval=10)
    while True:
        monitor.run()

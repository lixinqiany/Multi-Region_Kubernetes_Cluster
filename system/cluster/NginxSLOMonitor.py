# cluster/slo_monitor.py

"""
NginxSLOMonitor (多阈值版)
========================

采集 nginx-vts-exporter 指标，计算：
  • 非2xx 请求数量与比例
  • 每采样间隔总请求数
  • >50ms、>100ms、>200ms 慢请求数量与比例

输出 CSV：
  • data/slo/all_nginx.csv   —— 全局汇总
  • data/slo/<ip>-nginx.csv  —— 各实例明细（按 instance 标签分组）

可按需调整指标名称和标签常量。
"""

import os
import time
import logging
from prometheus_api_client import PrometheusConnect
from .ClusterMonitor import ClusterMonitor


class NginxSLOMonitor:
    """
    监控 Nginx-VTS-Exporter SLO，多阈值延时。

    Parameters
    ----------
    prom_url : str
        Prometheus 服务地址
    interval : int
        采样周期（秒）
    """

    # ---------- 指标 & 标签 常量 ----------
    REQ_METRIC         = "nginx_vts_server_requests_total"
    HIST_BUCKET_METRIC = "nginx_vts_server_request_duration_seconds_bucket"
    HIST_COUNT_METRIC  = "nginx_vts_server_request_duration_seconds_count"
    LABEL_INSTANCE     = "host"      # exporter 实例标签
    LABEL_CODE         = "code"          # HTTP 响应码分组
    CODE_TOTAL_REGEX   = '=~"1xx|2xx|3xx|4xx|5xx"'   # 总请求码匹配
    CODE_ERROR_REGEX   = '=~"1xx|3xx|4xx|5xx"'       # 非2xx匹配
    # 三个延时阈值
    THRESHOLDS = {
        "50ms":  "0.050",
        "100ms": "0.100",
        "200ms": "0.200",
    }
    # -----------------------------------------

    def __init__(self, prom_url: str, interval: int):
        self.prom = PrometheusConnect(url=prom_url, disable_ssl=True)
        self.interval = interval
        self.cluster = ClusterMonitor()  # 保留以备扩展
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        win = f"[1m]"

        # 全局总请求 & 非2xx
        self.q_total = (
            f'sum(increase({self.REQ_METRIC}{{{self.LABEL_CODE}{self.CODE_TOTAL_REGEX}}}{win}))'
        )
        self.q_non2xx = (
            f'sum(increase({self.REQ_METRIC}{{{self.LABEL_CODE}{self.CODE_ERROR_REGEX}}}{win}))'
        )

        # 全局慢请求：针对每个阈值动态构造
        self.q_slow_global = {
            name: (
                f'sum(increase({self.HIST_COUNT_METRIC}{win})) - '
                f'sum(increase({self.HIST_BUCKET_METRIC}{{le="{le}"}}{win}))'
            )
            for name, le in self.THRESHOLDS.items()
        }

        # 实例级总请求 & 非2xx
        self.q_total_i = (
            f'sum by({self.LABEL_INSTANCE}) (increase('
            f'{self.REQ_METRIC}{{{self.LABEL_CODE}{self.CODE_TOTAL_REGEX}}}{win}))'
        )
        self.q_non2xx_i = (
            f'sum by({self.LABEL_INSTANCE}) (increase('
            f'{self.REQ_METRIC}{{{self.LABEL_CODE}{self.CODE_ERROR_REGEX}}}{win}))'
        )

        # 实例级慢请求
        self.q_slow_instance = {
            name: (
                f'sum by({self.LABEL_INSTANCE}) (increase({self.HIST_COUNT_METRIC}{win})) - '
                f'sum by({self.LABEL_INSTANCE}) (increase('
                f'{self.HIST_BUCKET_METRIC}{{le="{le}"}}{win}))'
            )
            for name, le in self.THRESHOLDS.items()
        }

    def _query_val(self, promql: str) -> float:
        """执行 instant query，返回第一条 value 或 0.0。"""
        try:
            res = self.prom.custom_query(query=promql)
            return float(res[0]["value"][1]) if res else 0.0
        except Exception as e:
            self.logger.error(f"Prometheus query error: {e}")
            return 0.0

    def _query_map(self, promql: str) -> dict[str, float]:
        """执行按 instance 聚合查询，返回 {instance: value}。"""
        out: dict[str, float] = {}
        try:
            for m in self.prom.custom_query(query=promql):
                inst = m["metric"].get(self.LABEL_INSTANCE, "")
                out[inst] = float(m["value"][1])
        except Exception as e:
            self.logger.error(f"Prometheus query error: {e}")
        return out

    def _write_csv(self, path: str, header: str, row: list[str]):
        """
        将一行数据写入 CSV，若文件不存在则先写表头。
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        first = not os.path.exists(path)
        with open(path, "a") as f:
            if first:
                f.write(header + "\n")
            f.write(",".join(row) + "\n")

    def run(self):
        """
        循环采集并写 CSV：
          - data/slo/all_nginx.csv
          - data/slo/<instance-ip>-nginx.csv
        """
        # 构建表头：动态加入三个阈值列
        header = [
            "timestamp",
            "non2xx_count", "non2xx_percent",
            "total_requests"
        ]
        # 全局慢请求列
        for name in self.THRESHOLDS:
            header += [f"slow_count_{name}", f"slow_percent_{name}"]
        header_line = ",".join(header)

        self.logger.info("NginxSLOMonitor (multi-threshold) started.")
        while True:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")

            # 全局统计
            total = self._query_val(self.q_total)
            non2xx = self._query_val(self.q_non2xx)

            # 慢请求全局
            slow_vals = {name: self._query_val(q) for name, q in self.q_slow_global.items()}

            # 计算百分比
            non_pct = (non2xx / total * 100) if total else 0.0
            slow_pcts = {
                name: (slow_vals[name] / total * 100) if total else 0.0
                for name in self.THRESHOLDS
            }

            # 写全局 CSV
            row = [
                ts,
                str(int(non2xx)), f"{non_pct:.2f}",
                str(int(total))
            ]
            for name in self.THRESHOLDS:
                row += [str(int(slow_vals[name])), f"{slow_pcts[name]:.2f}"]
            self._write_csv("data/slo/all_nginx.csv", header_line, row)

            self.logger.debug(
                f"GLOBAL → total={total}, non2xx={non2xx}({non_pct:.2f}%), " +
                ", ".join(f"slow{n}={slow_vals[n]}({slow_pcts[n]:.2f}%)"
                          for n in self.THRESHOLDS)
            )

            # 实例级统计
            total_map = self._query_map(self.q_total_i)
            non_map   = self._query_map(self.q_non2xx_i)
            slow_maps = {
                name: self._query_map(q) for name, q in self.q_slow_instance.items()
            }

            for inst, tot in total_map.items():
                non = non_map.get(inst, 0.0)
                non_pct_i = (non / tot * 100) if tot else 0.0

                # 慢请求实例值与百分比
                inst_slow_vals = {name: slow_maps[name].get(inst, 0.0)
                                  for name in self.THRESHOLDS}
                inst_slow_pcts = {
                    name: (inst_slow_vals[name] / tot * 100) if tot else 0.0
                    for name in self.THRESHOLDS
                }

                ip = inst.split(":")[0]
                row_i = [
                    ts,
                    str(int(non)), f"{non_pct_i:.2f}",
                    str(int(tot))
                ]
                for name in self.THRESHOLDS:
                    row_i += [str(int(inst_slow_vals[name])),
                              f"{inst_slow_pcts[name]:.2f}"]
                self._write_csv(f"data/slo/{ip}-nginx.csv", header_line, row_i)

                self.logger.debug(
                    f"[{ip}] total={tot}, non2xx={non}({non_pct_i:.2f}%), " +
                    ", ".join(f"slow{n}={inst_slow_vals[n]}({inst_slow_pcts[n]:.2f}%)"
                              for n in self.THRESHOLDS)
                )

            time.sleep(self.interval)

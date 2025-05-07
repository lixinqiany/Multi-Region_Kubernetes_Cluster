# cluster/job_monitor.py

"""
JobMonitor
==========

监控 Job 资源：
  - 每周期记录所有 Job 的预期完成数、已完成数、当前活跃数。
  - 记录新增/删除的 Job 事件到 history 文件。
  - 当检测到 Job 从未完成到完成，写入一次运行时长记录。
输出：
  - data/job/<job-name>-status.csv
    header: timestamp,completions,succeeded,active,failed
  - data/job/job-history.csv
    header: timestamp,action,job
  - data/job/job-runtime.csv
    header: job,start_time,completion_time,duration_seconds
"""

import os, time, logging

from .ClusterMonitor import ClusterMonitor


class JobMonitor:
    """监控 Kubernetes Job 的状态、变更和运行时长。"""

    def __init__(self, interval: int, namespace: str = "default"):
        """
        :param interval: 采样间隔（秒）
        :param namespace: 目标命名空间
        """
        self.cluster = ClusterMonitor()
        self.interval = interval
        self.namespace = namespace
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        # 保存 Job 上一次的状态
        self.prev_jobs = set()
        # 保存已记录过完成时长的 Job
        self.completed = set()

    def _write_csv(self, path: str, header: str, row: list[str]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a") as f:
            if new:
                f.write(header + "\n")
            f.write(",".join(row) + "\n")

    def run(self):
        """循环监控 Job，记录状态、事件和完成时长。"""
        status_header = "timestamp,completions,succeeded,active,failed"
        history_header = "timestamp,action,job"
        runtime_header = "job,start_time,completion_time,duration_seconds"
        self.logger.info("Job资源监控器JobMonitor started.")
        while True:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")

            # 1) 获取当前 Job 列表及其状态
            jobs = self.cluster.list_jobs(self.namespace)
            curr_names = set(jobs.keys())

            # 2) 新增/删除事件记录
            added = curr_names - self.prev_jobs
            removed = self.prev_jobs - curr_names
            hist_path = "data/job/job-history.csv"
            for name in added:
                self._write_csv(hist_path, history_header, [ts, "ADD", name])
                self.logger.info(f"Job ADD {name}")
            for name in removed:
                self._write_csv(hist_path, history_header, [ts, "DEL", name])
                self.logger.info(f"Job DEL {name}")

            # 3) 状态记录 & 完成时长检测
            for name, info in jobs.items():
                comp = info.get("completions", 0)
                succ = info.get("succeeded", 0)
                active = info.get("active", 0)
                failed = info.get("failed", 0)
                row = [ts, str(comp), str(succ), str(active), str(failed)]
                path = f"data/job/{name}-status.csv"
                self._write_csv(path, status_header, row)
                self.logger.debug(
                    f"[{name}] completions={comp}, succeeded={succ}, active={active}, failed={failed}"
                )

                # 如果 Job 第一次完成且未记录过，写运行时长
                if succ >= comp > 0 and name not in self.completed:
                    start = info.get("start_time")
                    end = info.get("completion_time")
                    # 计算时长（秒）
                    duration = int((end - start).total_seconds()) if start and end else 0
                    rt_path = "data/job/job-runtime.csv"
                    rt_row = [
                        name,
                        start.isoformat() if start else "",
                        end.isoformat() if end else "",
                        str(duration)
                    ]
                    self._write_csv(rt_path, runtime_header, rt_row)
                    self.completed.add(name)
                    self.logger.info(f"Job COMPLETE {name}, duration={duration}s")

            # 4) 更新 prev_jobs
            self.prev_jobs = curr_names
            time.sleep(self.interval)

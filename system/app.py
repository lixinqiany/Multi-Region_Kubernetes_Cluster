import logging, threading, argparse, os, time
from cluster.NodeMonitor import NodeMonitor
from cluster.PodMonitor import PodMonitor
from cluster.NodePodMonitor import NodePodMonitor
from cluster.NginxSLOMonitor import NginxSLOMonitor
from cluster.DeploymentMonitor import DeploymentMonitor
from cluster.JobMonitor import JobMonitor

def main():
    # 入口参数
    parser = argparse.ArgumentParser(description="K8s Cluster Monitoring")
    parser.add_argument("--interval", type=int, default=5, help="采样间隔（秒）")
    parser.add_argument("--log", action="store_false", help="启用日志记录到文件")
    parser.add_argument("--prom", type=str, default="http://34.129.107.238:32501", help="Prometheus地址URL")
    args = parser.parse_args()

    # 日志配置
    if args.log:
        os.makedirs("logs", exist_ok=True)
        log_file = time.strftime("logs/%Y%m%d-%H%M%S.log")
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            encoding="utf-8",
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        # 默认仅警告级别以上输出至控制台（不创建文件）
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    prom_url = args.prom
    interval = args.interval
    logging.info(f"Starting monitors with interval={interval}s, Prometheus={prom_url}")
    # 初始化监控实例
    node_monitor = NodeMonitor(prom_url, interval)
    pod_monitor = PodMonitor(prom_url, interval)
    dist_monitor = NodePodMonitor(prom_url, interval)
    slo_monitor = NginxSLOMonitor(prom_url, interval)
    deploy_monitor = DeploymentMonitor(interval)
    job_monitor = JobMonitor(interval)
    monitors = [node_monitor, pod_monitor, dist_monitor, slo_monitor,deploy_monitor, job_monitor]

    # 创建并启动线程
    threads = []
    for monitor in monitors:
        t = threading.Thread(target=monitor.run, name=monitor.__class__.__name__, daemon=True)
        t.start()
        threads.append(t)
    logging.info("All monitoring threads started.")
    # 主线程保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping monitors...")
        # 若需优雅停止，可在各monitor中检查一个标志，此处简单退出
        # （由于daemon=True，主程序退出时线程也会结束）
        return


if __name__ == "__main__":
    main()
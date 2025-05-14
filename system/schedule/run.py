"""
调用gcp的module来创建实验环境
 - 虚拟机
 - 加入cluster
 - 前提：master已经存在
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from VMManager import VMManager
import os, logging

from system.schedule.rfsa_optimizer import RFSAOptimizer
from system.schedule.sa_optimizer import SAOptimizer
from system.schedule.scheduler import Scheduler

if __name__ == '__main__':
    os.makedirs("./logs", exist_ok=True)
    log_file = time.strftime("logs/%Y%m%d-%H%M%S.log")
    log_stored=True
    if log_stored:
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            encoding='utf-8',
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        # 默认仅警告级别以上输出至控制台（不创建文件）
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./config/single-cloud-ylxq-ed1608c43bb4.json"

    optimizer = RFSAOptimizer(
        price_json="data/gcp/region_machine_prices.json",
        spec_json="data/gcp/machine_types.json"
    )
    sa = SAOptimizer(seed_optimizer=optimizer,
                     n_iter=300,  # 每个温度邻域尝试次数
                     T0=60, Tmin=1, alpha=0.9)

    sched = Scheduler(optimizer=sa, interval_sec=60)  # 每 2 分钟调度一次
    sched.run_forever()
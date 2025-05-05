import asyncio
import aiohttp
import time
import logging
import argparse
import matplotlib.pyplot as plt
import signal
import sys
import math

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class PhaseStats:
    def __init__(self):
        self.reset()
        self.qps_records = []
        self.error_rate_records = []

    def reset(self):
        self.total_requests = 0
        self.success_requests = 0
        self.error_requests = 0
        self.last_success = 0
        self.last_errors = 0
        self.last_requests = 0

    def record_result(self, status):
        self.total_requests += 1
        self.last_requests += 1
        if status and 200 <= status < 300:
            self.success_requests += 1
            self.last_success += 1
        else:
            self.error_requests += 1
            self.last_errors += 1

    def snapshot(self, interval_sec):
        qps = self.last_success / interval_sec
        total = self.last_requests
        err_rate = (self.last_errors / total * 100) if total > 0 else 0.0
        self.qps_records.append(qps)
        self.error_rate_records.append(err_rate)
        return qps, err_rate

    def log_interval_summary(self, interval_sec):
        total = self.last_requests
        succ = self.last_success
        err = self.last_errors
        qps, err_rate = self.snapshot(interval_sec)
        succ_rate = (succ / total * 100) if total > 0 else 0.0
        logging.info(f"[Interval Summary] Total: {total}, Success: {succ} ({succ_rate:.1f}%), "
                     f"Errors: {err} ({err_rate:.1f}%), QPS: {qps:.2f}")
        self.last_success = 0
        self.last_errors = 0
        self.last_requests = 0

    def log_phase_summary(self, duration_sec):
        total = self.total_requests
        succ = self.success_requests
        err = self.error_requests
        qps = succ / duration_sec if duration_sec > 0 else 0
        succ_rate = (succ / total * 100) if total > 0 else 0.0
        err_rate = (err / total * 100) if total > 0 else 0.0
        logging.info(f"[Phase Summary] Duration: {duration_sec}s, Total: {total}, "
                     f"Success: {succ} ({succ_rate:.1f}%), Errors: {err} ({err_rate:.1f}%), "
                     f"Avg QPS: {qps:.2f}")

async def fetch(session, url):
    try:
        async with session.get(url) as response:
            await response.read()
            return response.status
    except:
        return None

async def run_sine_load(session, url, peak, base, period, stats: PhaseStats):
    interval = 2  # 每2秒采样一次正弦曲线
    slices = 10  # 每2秒内分成10批，每批间隔200ms发请求
    slice_interval = interval / slices
    start_time = time.time()

    async def ticker():
        while True:
            await asyncio.sleep(10)
            stats.log_interval_summary(10)

    asyncio.create_task(ticker())

    while True:
        t = time.time() - start_time
        # 用 2π 控制完整周期
        qps = base + (peak - base) * math.sin(2 * math.pi * t / period)
        qps = max(0, qps)
        total_requests = int(qps * interval)
        batch_size = total_requests // slices

        logging.info(f"[Wave] t={int(t)}s  -> QPS Target ≈ {int(qps)} -> Requests in next 2s = {total_requests}")

        for _ in range(slices):
            for _ in range(batch_size):
                task = asyncio.create_task(fetch(session, url))
                task.add_done_callback(lambda fut: stats.record_result(fut.result()))
            await asyncio.sleep(slice_interval)

async def run(url, peak, base, period, stats: PhaseStats):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=None)) as session:
        await run_sine_load(session, url, peak, base, period, stats)

def plot_qps_and_error_rate(qps_records, error_rate_records):
    times = list(range(1, len(qps_records) + 1))
    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.set_xlabel('Time (10s intervals)')
    ax1.set_ylabel('QPS', color='tab:blue')
    ax1.plot(times, qps_records, label='QPS', color='tab:blue')
    ax1.tick_params(axis='y', labelcolor='tab:blue')
    ax1.grid(True)

    ax2 = ax1.twinx()
    ax2.set_ylabel('Error Rate (%)', color='tab:red')
    ax2.plot(times, error_rate_records, label='Error Rate (%)', color='tab:red')
    ax2.tick_params(axis='y', labelcolor='tab:red')
    ax2.set_ylim(0, 100)

    fig.tight_layout()
    plt.title('QPS and Error Rate Over Time')
    plt.savefig("qps_error_rate_plot.png")
    logging.info("Saved plot to qps_error_rate_plot.png")

def main():
    parser = argparse.ArgumentParser(description="Sine wave smoothed load generator")
    parser.add_argument('--url', type=str, default='http://34.129.107.238:30080/', help='Target URL')
    parser.add_argument('--peak', type=int, default=1200, help='Peak QPS')
    parser.add_argument('--base', type=int, default=800, help='Base QPS')
    parser.add_argument('--period', type=int, default=120, help='Sine wave period in seconds')

    args = parser.parse_args()
    stats = PhaseStats()

    def signal_handler(sig, frame):
        logging.info("Interrupted! Generating plot and exiting...")
        plot_qps_and_error_rate(stats.qps_records, stats.error_rate_records)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    logging.info(f"Starting sine wave load test to {args.url}")
    asyncio.run(run(args.url, args.peak, args.base, args.period, stats))

if __name__ == '__main__':
    main()

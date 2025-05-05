import asyncio
import aiohttp
import time
import logging
import argparse
import matplotlib.pyplot as plt
import signal
import sys

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
        self.last_10s_success = 0
        self.last_10s_errors = 0
        self.last_10s_requests = 0

    def record_result(self, status):
        self.total_requests += 1
        self.last_10s_requests += 1
        if status and 200 <= status < 300:
            self.success_requests += 1
            self.last_10s_success += 1
        else:
            self.error_requests += 1
            self.last_10s_errors += 1

    def snapshot(self, interval_sec):
        qps = self.last_10s_success / interval_sec
        total = self.last_10s_requests
        err_rate = (self.last_10s_errors / total * 100) if total > 0 else 0.0
        self.qps_records.append(qps)
        self.error_rate_records.append(err_rate)
        return qps, err_rate

    def log_interval_summary(self, interval_sec):
        total = self.last_10s_requests
        succ = self.last_10s_success
        err = self.last_10s_errors
        qps, err_rate = self.snapshot(interval_sec)
        succ_rate = (succ / total * 100) if total > 0 else 0.0
        logging.info(f"[10s Summary] Total: {total}, Success: {succ} ({succ_rate:.1f}%), "
                     f"Errors: {err} ({err_rate:.1f}%), QPS: {qps:.2f}")
        self.last_10s_success = 0
        self.last_10s_errors = 0
        self.last_10s_requests = 0

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

async def run_phase(session, url, rate, duration, stats: PhaseStats):
    batch_interval = 0.05  # 每 10ms 发一批
    batch_size = max(1, int(rate * batch_interval))  # 每批多少个请求
    start_time = time.time()
    end_time = start_time + duration

    async def ticker():
        while time.time() < end_time:
            await asyncio.sleep(10)
            stats.log_interval_summary(10)

    asyncio.create_task(ticker())

    while time.time() < end_time:
        tasks = []
        for _ in range(batch_size):
            task = asyncio.create_task(fetch(session, url))
            task.add_done_callback(lambda fut: stats.record_result(fut.result()))
            tasks.append(task)
        await asyncio.sleep(batch_interval)

    stats.log_phase_summary(duration)


async def run_forever(url, high_rate, low_rate, phase_duration, stats: PhaseStats):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=None)) as session:
        while True:
            logging.info(f"--- High Rate Phase ({high_rate} r/s) ---")
            stats.reset()
            await run_phase(session, url, high_rate, phase_duration, stats)

            logging.info(f"--- Low Rate Phase ({low_rate} r/s) ---")
            stats.reset()
            await run_phase(session, url, low_rate, phase_duration, stats)

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
    ax2.set_ylim(0,100)

    fig.tight_layout()
    plt.title('QPS and Error Rate Over Time')
    plt.savefig("qps_error_rate_plot.png")
    logging.info("Saved plot to qps_error_rate_plot.png")

def main():
    parser = argparse.ArgumentParser(description="Async stepped load generator with infinite loop")
    parser.add_argument('--url', type=str, default='http://34.129.107.238:30080', help='Target URL')
    parser.add_argument('--high', type=int, default=700, help='High QPS')
    parser.add_argument('--low', type=int, default=300, help='Low QPS')
    parser.add_argument('--duration', type=int, default=60, help='Phase duration in seconds')

    args = parser.parse_args()
    stats = PhaseStats()

    def signal_handler(sig, frame):
        logging.info("Interrupted! Generating plot and exiting...")
        plot_qps_and_error_rate(stats.qps_records, stats.error_rate_records)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    logging.info(f"Starting stepped load test to {args.url}")
    asyncio.run(run_forever(args.url, args.high, args.low, args.duration, stats))

if __name__ == '__main__':
    main()

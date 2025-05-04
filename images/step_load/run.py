#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import concurrent
import time
import requests
import signal
import sys
import argparse
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor, as_completed

# 全局统计变量
TOTAL_REQUESTS = 0
TOTAL_SUCCESS = 0
TOTAL_ERRORS = 0

# 存放所有记录间隔（interval）采样的 QPS 点
ALL_QPS_POINTS = []

def send_request(session, url):
    """
    发送单个请求，返回 (status_code, elapsed_time)
    """
    try:
        resp = session.get(url, timeout=5)
        return resp.status_code, resp.elapsed.total_seconds()
    except requests.exceptions.RequestException:
        # 若出现网络异常等，视为错误
        return None, None


def run_step_test(target_url, step_rate, step_duration, record_interval=10):
    global TOTAL_REQUESTS, TOTAL_SUCCESS, TOTAL_ERRORS

    start_time = time.time()
    step_end_time = start_time + step_duration
    max_total_requests = step_rate * step_duration

    stage_requests = 0
    stage_success = 0
    stage_errors = 0

    interval_start_time = start_time
    interval_requests = 0
    interval_success = 0
    interval_errors = 0
    interval_samples = []

    session = requests.Session()
    executor = ThreadPoolExecutor(max_workers=step_rate * 2)
    pending_futures = []

    batch_interval = 0.1
    batch_size = max(1, int(step_rate * batch_interval))
    next_batch_time = start_time

    # 主循环严格在阶段时间内运行
    while time.time() < step_end_time:
        current_time = time.time()

        # 动态提交批次请求（严格不超发）
        while current_time >= next_batch_time:
            remaining_quota = max_total_requests - (stage_requests + len(pending_futures))
            if remaining_quota <= 0:
                break

            current_batch_size = min(batch_size, remaining_quota)
            for _ in range(current_batch_size):
                future = executor.submit(send_request, session, target_url)
                pending_futures.append(future)

            next_batch_time += batch_interval
            current_time = time.time()

        # 非阻塞获取已完成请求
        if pending_futures:
            done, pending = concurrent.futures.wait(
                pending_futures,
                timeout=0,
                return_when=concurrent.futures.FIRST_COMPLETED
            )
            pending_futures = list(pending)
        else:
            done = []

        # 统计完成结果
        for future in done:
            status_code, _ = future.result()
            stage_requests += 1
            interval_requests += 1
            TOTAL_REQUESTS += 1

            if status_code and 200 <= status_code < 300:
                stage_success += 1
                interval_success += 1
                TOTAL_SUCCESS += 1
            else:
                stage_errors += 1
                interval_errors += 1
                TOTAL_ERRORS += 1

        # 记录间隔统计
        interval_elapsed = current_time - interval_start_time
        if interval_elapsed >= record_interval:
            current_qps = interval_requests / interval_elapsed if interval_elapsed > 0 else 0
            success_rate = (interval_success / interval_requests * 100) if interval_requests > 0 else 0
            error_rate = (interval_errors / interval_requests * 100) if interval_requests > 0 else 0

            print(f"[{time.strftime('%H:%M:%S')}] "
                  f"StepRate={step_rate} r/s, Interval={interval_elapsed:.1f}s, "
                  f"Req={interval_requests}, Succ={interval_success} ({success_rate:.1f}%), "
                  f"Err={interval_errors} ({error_rate:.1f}%), QPS={current_qps:.2f}")

            interval_samples.append({
                "timestamp": current_time,
                "requests": interval_requests,
                "success": interval_success,
                "errors": interval_errors,
                "qps": current_qps
            })

            interval_start_time = current_time
            interval_requests = 0
            interval_success = 0
            interval_errors = 0

        # 动态等待时间
        sleep_time = next_batch_time - time.time()
        if sleep_time > 0:
            time.sleep(max(0, sleep_time * 0.95))

    # 阶段结束后丢弃未完成请求
    executor.shutdown(wait=False)
    session.close()

    # 强制记录最后一个间隔
    if interval_requests > 0:
        final_interval_elapsed = time.time() - interval_start_time
        current_qps = interval_requests / final_interval_elapsed if final_interval_elapsed > 0 else 0
        success_rate = (interval_success / interval_requests * 100) if interval_requests > 0 else 0
        error_rate = (interval_errors / interval_requests * 100) if interval_requests > 0 else 0

        print(f"[{time.strftime('%H:%M:%S')}] "
              f"StepRate={step_rate} r/s (Final), Interval={final_interval_elapsed:.1f}s, "
              f"Req={interval_requests}, Succ={interval_success} ({success_rate:.1f}%), "
              f"Err={interval_errors} ({error_rate:.1f}%), QPS={current_qps:.2f}")

        interval_samples.append({
            "timestamp": time.time(),
            "requests": interval_requests,
            "success": interval_success,
            "errors": interval_errors,
            "qps": current_qps
        })

    # 最终统计（不强制对齐目标值）
    stage_elapsed = time.time() - start_time
    stage_qps = stage_requests / stage_elapsed if stage_elapsed > 0 else 0
    print(f"\n=== Finished stage (Rate={step_rate} r/s) ===")
    print(f"Design Duration: {step_duration}s, Actual Duration: {stage_elapsed:.1f}s")
    print(f"TotalRequests: {stage_requests} (Target: {max_total_requests}), Avg QPS={stage_qps:.2f}\n")

    return {
        "stage_requests": stage_requests,
        "stage_success": stage_success,
        "stage_errors": stage_errors,
        "samples": interval_samples
    }

def signal_handler(sig, frame):
    """
    捕获 Ctrl+C (SIGINT) 信号后，打印最后的总统计报告和QPS曲线，然后退出程序。
    """
    print("\n=== Program interrupted! Printing final summary... ===\n")
    print_final_summary_and_exit()


def print_final_summary_and_exit():
    """
    打印程序运行期的总体统计信息，并绘制整体QPS变化图，然后退出。
    """
    global TOTAL_REQUESTS, TOTAL_SUCCESS, TOTAL_ERRORS, ALL_QPS_POINTS

    # 总体统计
    success_rate_overall = (TOTAL_SUCCESS / TOTAL_REQUESTS * 100) if TOTAL_REQUESTS > 0 else 0
    error_rate_overall = (TOTAL_ERRORS / TOTAL_REQUESTS * 100) if TOTAL_REQUESTS > 0 else 0

    print("=== Overall Summary ===")
    print(f"Total Requests: {TOTAL_REQUESTS}")
    print(f"Success: {TOTAL_SUCCESS} ({success_rate_overall:.1f}%)")
    print(f"Errors: {TOTAL_ERRORS} ({error_rate_overall:.1f}%)\n")

    # 如果没有采样数据，直接退出
    if len(ALL_QPS_POINTS) == 0:
        print("No QPS data to plot. Exiting.")
        sys.exit(0)

    # 按时间顺序排序
    ALL_QPS_POINTS.sort(key=lambda x: x["timestamp"])
    start_timestamp = ALL_QPS_POINTS[0]["timestamp"]
    times = [pt["timestamp"] - start_timestamp for pt in ALL_QPS_POINTS]
    qps_values = [pt["qps"] for pt in ALL_QPS_POINTS]
    error_rates = [(pt["errors"] / pt["requests"] * 100) if pt["requests"] > 0 else 0
                   for pt in ALL_QPS_POINTS]

    # 创建双轴绘图
    fig, ax1 = plt.subplots(figsize=(10, 6))

    # 绘制QPS曲线
    ax1.plot(times, qps_values, 'b-', marker='o', label='QPS')
    ax1.set_xlabel('Time (s) from start')
    ax1.set_ylabel('QPS', color='b')
    ax1.tick_params('y', colors='b')
    ax1.grid(True)

    # 创建第二个y轴用于错误率
    ax2 = ax1.twinx()
    ax2.plot(times, error_rates, 'r--', marker='s', label='Error Rate')
    ax2.set_ylabel('Error Rate (%)', color='r')
    ax2.tick_params('y', colors='r')
    ax2.set_ylim(0, 50)  # 新增的坐标轴范围限制

    # 设置标题和图例
    plt.title("QPS and Error Rate Over Time")
    fig.tight_layout()

    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')

    plt.show()

    sys.exit(0)

def main():
    # 使用 argparse 来解析命令行参数
    parser = argparse.ArgumentParser(description="Infinite load test script with high-low stepping.")
    parser.add_argument("--url", type=str, default="http://localhost:80",
                        help="Target URL for load testing.")
    parser.add_argument("--low", type=int, default=150,
                        help="Low request rate (requests per second).")
    parser.add_argument("--high", type=int, default=500,
                        help="High request rate (requests per second).")
    parser.add_argument("--duration", type=int, default=30,
                        help="Duration (in seconds) for each stage (high or low).")
    parser.add_argument("--interval", type=int, default=10,
                        help="Record interval (in seconds) for printing partial stats.")
    args = parser.parse_args()

    # 捕获 Ctrl+C 信号，以便中断时做最终汇总
    signal.signal(signal.SIGINT, signal_handler)

    print(f"Starting infinite load test on {args.url} ...")
    print(f"High Rate = {args.high} r/s, Low Rate = {args.low} r/s, Stage Duration = {args.duration}s")
    print("Press Ctrl+C to stop and see the final summary.\n")

    global ALL_QPS_POINTS

    cycle_index = 1
    while True:
        print(f"=== Starting cycle #{cycle_index} ===")

        # 1) 先执行高速率阶段
        print(f"Switching to HIGH rate = {args.high} r/s")
        high_step = run_step_test(
            target_url=args.url,
            step_rate=args.high,
            step_duration=args.duration,
            record_interval=args.interval
        )
        ALL_QPS_POINTS.extend(high_step["samples"])

        # 2) 再执行低速率阶段
        print(f"Switching to LOW rate = {args.low} r/s")
        low_step = run_step_test(
            target_url=args.url,
            step_rate=args.low,
            step_duration=args.duration,
            record_interval=args.interval
        )
        ALL_QPS_POINTS.extend(low_step["samples"])

        cycle_index += 1


if __name__ == "__main__":
    main()
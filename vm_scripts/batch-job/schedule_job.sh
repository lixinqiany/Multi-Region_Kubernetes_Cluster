#!/usr/bin/env bash
set -euo pipefail

# 等待指定 Job 完成
# 参数：$1 = job 名称，$2 = 超时时间（秒，可选，默认 3600s）
wait_for_completion() {
  local job="$1"
  local timeout="${2:-3600}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 等待 Job '$job' 完成，超时 ${timeout}s ..."
  kubectl wait --for=condition=complete "job/${job}" --timeout="${timeout}s"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Job '$job' 已完成。"
}

# 第一次轮次
echo "[$(date '+%Y-%m-%d %H:%M:%S')] T₀+10m：apply renaissance.yaml"
sleep 600
kubectl apply -f renaissance.yaml

echo "[$(date '+%Y-%m-%d %H:%M:%S')] T₀+20m：apply mbw.yaml"
sleep 600
kubectl apply -f mbw.yaml

# 第二次轮次
echo "[$(date '+%Y-%m-%d %H:%M:%S')] T₀+65m：准备第二轮 renaissance"
sleep 3000

# 等待上一轮 renaissance 完成，再 delete 并重新 apply
wait_for_completion "renaissance"
kubectl delete job renaissance --ignore-not-found
sleep 5
kubectl apply -f renaissance.yaml
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 第二轮 renaissance 已提交。"

# 再等 10 分钟，第二轮 mbw
echo "[$(date '+%Y-%m-%d %H:%M:%S')] T₀+75m：准备第二轮 mbw"
sleep 600

# 等待上一轮 mbw 完成，再 delete 并重新 apply
wait_for_completion "mbw"
kubectl delete job mbw --ignore-not-found
sleep 5
kubectl apply -f mbw.yaml
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 第二轮 mbw 已提交。"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 脚本执行完毕。"

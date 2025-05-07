#!/bin/bash

# 全局最大时长（秒）：12 分钟
MAX_DURATION=$((12 * 60))
START_TS=$(date +%s)

LOG_DIR="/root"
mkdir -p "$LOG_DIR"
iteration=0

while true; do
  NOW_TS=$(date +%s)
  ELAPSED=$(( NOW_TS - START_TS ))
  # 全局超时检查
  if [ "$ELAPSED" -ge "$MAX_DURATION" ]; then
    echo "[INFO] 全局已运行 ${ELAPSED}s（≥${MAX_DURATION}s），安全退出"
    exit 0
  fi

  iteration=$((iteration + 1))
  REMAIN=$(( MAX_DURATION - ELAPSED ))
  echo "=== Iteration #${iteration} — 已用 ${ELAPSED}s，剩余 ${REMAIN}s ==="

  TIMESTAMP=$(date +%Y%m%d%H%M%S)
  LOG_FILE="$LOG_DIR/renaissance-${TIMESTAMP}.log"
  echo "*** Start Renaissance Auto Test (iter ${iteration}) ***" | tee -a "$LOG_FILE"

  # 用 timeout 包裹单次调用，限制最长为剩余全局时间
  timeout --preserve-status "${REMAIN}s" ./response.sh 2>&1 | tee -a "$LOG_FILE"
  RESP_EXIT=${PIPESTATUS[0]}

  # 如果是超时（exit code 124），当作全局超时，正常退出
  # 如果是 124（timeout 默认）或 143（SIGTERM），都当作安全超时，退出 0
  if [[ "$RESP_EXIT" -eq 124 || "$RESP_EXIT" -eq 143 ]]; then
    echo "[INFO] 单次测试达到剩余全局时长 ${REMAIN}s（exit ${RESP_EXIT}），安全退出" | tee -a "$LOG_FILE"
    exit 0
  fi

  # 其它非 0 错误，终止脚本
  if [ "$RESP_EXIT" -ne 0 ]; then
    echo "[ERROR] response.sh 退出码 ${RESP_EXIT}，终止循环" | tee -a "$LOG_FILE"
    exit "$RESP_EXIT"
  fi

  # 获取最新结果目录并转成 JSON
  RESULTS_DIR="/var/lib/phoronix-test-suite/test-results"
  LATEST=$(ls -t "$RESULTS_DIR" | head -1)
  if [ -z "$LATEST" ]; then
    echo "[ERROR] 未找到测试结果目录" | tee -a "$LOG_FILE"
    exit 1
  fi

  echo "*** JSON Conversion for ${LATEST} ***" | tee -a "$LOG_FILE"
  phoronix-test-suite result-file-to-json "$LATEST" 2>&1 | tee -a "$LOG_FILE"
  JSON_EXIT=${PIPESTATUS[0]}
  if [ "$JSON_EXIT" -ne 0 ]; then
    echo "[ERROR] JSON 转换失败（${JSON_EXIT}）" | tee -a "$LOG_FILE"
    exit "$JSON_EXIT"
  fi

  echo "*** Iteration #${iteration} completed ***" | tee -a "$LOG_FILE"
done

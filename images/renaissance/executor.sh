#!/bin/bash

LOG_DIR="/root"
LOG_FILE="$LOG_DIR/execution-$(date +%Y%m%d%H%M%S).log"
# mkdir -p "$LOG_DIR"

# 捕获所有输出到日志文件（同时显示在终端）
exec > >(tee -a "$LOG_FILE") 2>&1

# 步骤 1: 运行自动化测试并捕获结果名称
echo "***Start C-ray Auto Test***"
./response.sh

# 步骤 2: 获取最新生成的结果名称
RESULTS_DIR="/var/lib/phoronix-test-suite/test-results"
LATEST_RESULT=$(ls -t "$RESULTS_DIR" | head -1)

if [ -z "$LATEST_RESULT" ]; then
  echo "[ERROR] 未找到测试结果目录，请检查测试是否成功运行"
  exit 1
fi

# 步骤 3: 转换结果到 JSON 并保存到当前目录
echo "***JSON Conversion***"
phoronix-test-suite result-file-to-json "$LATEST_RESULT"

#结果在/root/coremark-autotest.json

exit 0
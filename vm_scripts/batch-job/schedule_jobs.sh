#!/bin/bash

# 输出开始时间
echo "🕒 $(date): 脚本开始执行。将于 15 分钟后部署 renaissance-job..."

# 等待 15 分钟（900 秒）
sleep 900

# 应用 renaissance job
echo "🚀 $(date): 正在部署 renaissance-job.yaml..."
kubectl apply -f renaissance-job.yaml

# 等待 30 分钟（1800 秒）
echo "🕒 $(date): 等待 30 分钟再部署 mbw-job..."
sleep 900

# 应用 mbw job
echo "🚀 $(date): 正在部署 mbw-job.yaml..."
kubectl apply -f mbw-job.yaml

echo "✅ $(date): 所有作业部署完成。"
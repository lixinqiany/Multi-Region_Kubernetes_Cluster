#!/usr/bin/env bash

# 第一次轮次
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 等待 10 分钟，开始 apply renaissance.yaml"
sleep 600
kubectl apply -f renaissance.yaml

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 等待 10 分钟，开始 apply mbw.yaml"
sleep 600
kubectl apply -f mbw.yaml

# 第二次轮次
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 等待 45 分钟，开始第二轮 apply renaissance.yaml"
sleep 3000
kubectl apply -f renaissance.yaml

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 等待 10 分钟，开始第二轮 apply mbw.yaml"
sleep 600
kubectl apply -f mbw.yaml

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 脚本执行完毕"

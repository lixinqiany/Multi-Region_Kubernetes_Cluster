#!/bin/bash

# 每阶段持续时间（秒），90分钟总共6个阶段 => 每阶段15分钟
DURATION=1200

# 阶跃副本数序列
REPLICAS=(4 10 4 8 6 4)

for r in "${REPLICAS[@]}"; do
  echo "$(date): Scaling step-load to $r replicas"
  kubectl scale deploy step-load --replicas=$r
  sleep $DURATION
done

echo "$(date): Done scaling. Total duration: $((DURATION * ${#REPLICAS[@]} / 60)) minutes"
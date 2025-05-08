#!/bin/bash

# 每阶段持续时间（秒）：15分钟
DURATION=900

# 两种副本数交替变化：6 和 10
LOW=6
HIGH=10

while true; do
  echo "$(date): Scaling step-load to $LOW replicas"
  kubectl scale deploy step-load --replicas=$LOW
  sleep $DURATION

  echo "$(date): Scaling step-load to $HIGH replicas"
  kubectl scale deploy step-load --replicas=$HIGH
  sleep $DURATION
done

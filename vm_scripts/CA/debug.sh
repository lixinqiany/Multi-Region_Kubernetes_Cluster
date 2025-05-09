#!/bin/bash
set -euo pipefail

# 固定使用的 GCP 项目和 zone
PROJECT="single-cloud-ylxq"
ZONE="australia-southeast1-b"

echo "Starting continuous providerID patch loop (PROJECT=${PROJECT}, ZONE=${ZONE})"
echo "Press Ctrl+C to stop."

while true; do
  # 找出所有 providerID 为空的节点
  missing=$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.spec.providerID}{"\n"}{end}' \
            | awk -F'|' '$2=="" { print $1 }')

  if [[ -n "$missing" ]]; then
    for node in $missing; do
      ts=$(date +"%Y-%m-%dT%H:%M:%S%z")
      echo "${ts}: Node '${node}' missing providerID, patching..."
      kubectl patch node "${node}" --type=merge -p \
        "{\"spec\":{\"providerID\":\"gce://${PROJECT}/${ZONE}/${node}\"}}"
      echo "${ts}: Patched ${node} → providerID=gce://${PROJECT}/${ZONE}/${node}"
    done
  fi

  # 短暂休眠后继续下一轮
  sleep 30
done
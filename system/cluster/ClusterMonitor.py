import logging

import urllib3
from kubernetes import client, config
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ClusterMonitor:
    """通过Kubernetes API与集群交互"""
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config("./config/config")
            self.logger.info(f"在本地连接到远程集群")

        self.core_v1 = client.CoreV1Api()

    def get_running_pods(self, namespace):
        """
        被PodMonitor调用获得命名空间下正在running的pod名字集合
        """
        pod_set = set()
        try:
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace,
                field_selector="status.phase=Running"
            )
            for p in pods.items:
                pod_set.add(p.metadata.name)
        except Exception as e:
            self.logger.error(f"在获取{namespace}空间下正在running的pod名字集合时发生报错\n{e}")

        return pod_set

    def get_pod_node_map(self, namespace: str = "default"):
        """
        返回指定命名空间下每个节点(node)对应的 Running Pod 名称集合：
            { node_name: { pod1, pod2, ... }, ... }

        被NodePodMonitor调用
        """
        mapping = {}
        try:
            resp = self.core_v1.list_namespaced_pod(
                namespace=namespace,
                field_selector="status.phase=Running"
            )
            for p in resp.items:
                node = p.spec.node_name or "<unknown>"
                mapping.setdefault(node, set()).add(p.metadata.name)
        except Exception as e:
            self.logger.error(f"获取 Pod-Node 映射失败: {e}")
        return mapping

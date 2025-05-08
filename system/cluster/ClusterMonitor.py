import logging
import time

import urllib3
from kubernetes import client, config
from kubernetes.client import PolicyV1Api
from kubernetes.client.rest import ApiException
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
        self.batch_v1 = client.BatchV1Api()
        self.apps_v1 = client.AppsV1Api()

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

    def get_node_internal_ips(self) -> dict[str, str]:
        """
        获取集群中每个 Node 的 InternalIP：
        返回  { node_name: internal_ip, ... }

        被NodePodMonitor调用
        """
        ip_map: dict[str, str] = {}
        try:
            nodes = self.core_v1.list_node().items
            for n in nodes:
                name = n.metadata.name
                ip = "<unknown>"
                for a in n.status.addresses:
                    if a.type == "InternalIP":
                        ip = a.address
                        break
                ip_map[name] = ip
        except Exception as exc:
            self.logger.error(f"获取 Node IP 失败: {exc}")
        return ip_map

    def list_deployments(self, namespace: str = "default"):
        """
        列出 Namespace 下所有 Deployment 并返回状态字典：
          { name: {"desired": int, "available": int, "ready": int}, ... }
        """
        out = {}
        try:
            resp = self.apps_v1.list_namespaced_deployment(namespace)
            for d in resp.items:
                name = d.metadata.name
                out[name] = {
                    "desired": d.spec.replicas or 0,
                    "available": d.status.available_replicas or 0,
                    "ready": d.status.ready_replicas or 0
                }
        except Exception as e:
            self.logger.error(f"list_deployments failed: {e}")
        return out

    def list_jobs(self, namespace: str = "default"):
        """
        列出 Namespace 下所有 Job 并返回状态字典：
          { name: {
                "completions": spec.completions,
                "succeeded": status.succeeded,
                "active": status.active,
                "failed": status.failed,
                "start_time": status.start_time (datetime),
                "completion_time": status.completion_time (datetime)
            }, ... }
        """
        out = {}
        try:
            resp = self.batch_v1.list_namespaced_job(namespace)
            for j in resp.items:
                name = j.metadata.name
                out[name] = {
                    "completions": j.spec.completions or 0,
                    "succeeded": j.status.succeeded or 0,
                    "active": j.status.active or 0,
                    "failed": j.status.failed or 0,
                    "start_time": j.status.start_time,
                    "completion_time": j.status.completion_time
                }
        except Exception as e:
            self.logger.error(f"list_jobs failed: {e}")
        return out

    def cordon_node(self, node_name: str):
        """
        Mark a node as unschedulable (cordon).
        """
        body = {"spec": {"unschedulable": True}}
        try:
            self.core_v1.patch_node(node_name, body)
            self.logger.info(f"Cordoned node {node_name},标记不可调度")
        except ApiException as e:
            self.logger.error(f"Failed to cordon {node_name}: {e}")
            raise

    def drain_node(self, node_name: str, grace_period_seconds: int = 30, timeout: int = 120):
        """
        Evict all pods from the node, blocking until done or timeout.
        """
        # 先 cordon
        self.cordon_node(node_name)

        # 一次性获取所有 Pod
        field = f"spec.nodeName={node_name}"
        pods = self.core_v1.list_pod_for_all_namespaces(field_selector=field).items
        if not pods:
            self.logger.info(f"没有pods No pods found on node {node_name} to evict.")
            return

        for pod in pods:
            name = pod.metadata.name
            ns = pod.metadata.namespace
            eviction = client.V1Eviction(
                metadata=client.V1ObjectMeta(name=name, namespace=ns),
                delete_options=client.V1DeleteOptions(grace_period_seconds=grace_period_seconds)
            )
            try:
                # 尝试优雅驱逐
                self.core_v1.create_namespaced_pod_eviction(
                    name=name, namespace=ns, body=eviction
                )
                self.logger.debug(f"Eviction triggered for Pod {name}")
            except ApiException as e:
                # PDB 阻止或其它错误，则强制删除
                self.logger.warning(f"Eviction failed for {name} (status {e.status}), deleting directly")
                try:
                    self.core_v1.delete_namespaced_pod(
                        name=name,
                        namespace=ns,
                        grace_period_seconds=grace_period_seconds,
                        body=client.V1DeleteOptions()
                    )
                    self.logger.debug(f"Deleted Pod {name} bypassing Eviction")
                except ApiException as delete_err:
                    self.logger.error(f"Failed to delete Pod {name}: {delete_err}")

        self.logger.info(f"Eviction/delete attempted once for all pods on node {node_name}")

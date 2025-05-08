"""
gcp_node_manager.py

基于价格约束的多区域 GCP VM 动态管理模块

功能：
  1. 加载定价数据（region_machine_prices.json、machine_types.json、pricing_map.json）
  2. 根据上层算法选出的 (region, zone, machine_type) 创建 VM 并通过 startup script 自动加入 Kubernetes 集群
  3. 优雅地从集群中退出节点，并删除对应的 VM
  4. 提供等待节点就绪的机制

依赖：
  pip install google-cloud-compute kubernetes
"""

import os, json, time, logging
from google.api_core.exceptions import NotFound
import paramiko
from google.cloud import compute_v1
from cluster.ClusterMonitor import ClusterMonitor
from pathlib import Path


class VMManager:
    """
        管理 GCP VM 节点的创建与删除，并通过 ClusterMonitor
        将它们加入 / 退出 Kubernetes 集群。
    """

    def __init__(self,
                 startup_script_path: str = './config/worker_initial.sh'):
        self.cluster_monitor = ClusterMonitor()
        self.instances_client = compute_v1.InstancesClient()
        self.machine_types_client = compute_v1.MachineTypesClient()
        # 用于获取可用 zone 列表
        self.regions_client = compute_v1.RegionsClient()

        self.project = "single-cloud-ylxq"
        project_root = Path(__file__).parents[1]  # system/gcp/VMManager.py -> ../../
        self.startup_script_path = str((project_root / startup_script_path).resolve())
        print(f"Startup script path: {self.startup_script_path}")

        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def _choose_zone(self, region: str, machine_type: str) -> str:
        """
        自动选择一个可用 zone，其中指定的 machine_type 存在。
        遍历 region 下所有 zones，返回第一个支持该机型的 zone。
        """
        region_info = self.regions_client.get(project=self.project, region=region)
        for zone_url in region_info.zones:
            zone = zone_url.split('/')[-1]
            # 检查 machine_type 是否可用
            request = compute_v1.ListMachineTypesRequest(project=self.project, zone=zone)
            for mt in self.machine_types_client.list(request=request):
                if mt.name == machine_type:
                    self.logger.info(f"在 zone {zone} 找到机型 {machine_type}")
                    return zone
        raise ValueError(f"在 region {region} 未找到可用的机型 {machine_type}")

    def create_node(
            self,
            name: str,
            location: str,
            machine_type: str,
            disk_size_gb: int = 20
    ):
        """
        创建 VM 并加入集群。
        location: 区域(region)或可用区(zone)。
        """
        if len(location.split('-')) == 2:
            zone = self._choose_zone(location, machine_type)
            self.logger.info(f"Location {location} 识别为 region，选用 zone {zone}")
        else:
            zone = location
        self.logger.info(f"准备创建 VM {name} (zone={zone}, type={machine_type})...")
        instance = compute_v1.Instance(
            name=name,
            machine_type=f"zones/{zone}/machineTypes/{machine_type}",
            disks=[compute_v1.AttachedDisk(
                auto_delete=True,
                boot=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image="projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20250415",
                    disk_size_gb=disk_size_gb
                )
            )],
            network_interfaces=[compute_v1.NetworkInterface(
                subnetwork=f"projects/single-cloud-ylxq/regions/{location}/subnetworks/single-cloud-vpc",
                access_configs=[
                    compute_v1.AccessConfig(
                        name="External NAT",
                        type="ONE_TO_ONE_NAT"
                    )
                ],
                stack_type="IPV4_ONLY"
            )],
            metadata=compute_v1.Metadata(items=[
                compute_v1.Items(
                    key="ssh-keys",
                    value=f"root:ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKlKzCy4htWLghJGtK6W+ojkXEaQCZvxX4Me/sbPlpJG 13160@michael_win"
                )
            ]),
            service_accounts=[
                compute_v1.ServiceAccount(
                    email="883507821345-compute@developer.gserviceaccount.com",
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            ]
        )
        op = self.instances_client.insert(project=self.project, zone=zone, instance_resource=instance)
        op.result()
        # wait for the instance to be running
        for _ in range(30):  # 最多等待 5 分钟
            inst = self.instances_client.get(project=self.project, zone=zone, instance=name)
            if inst.status == 'RUNNING':
                self.logger.info(f"VM {name} 已运行（RUNNING）。")
                break
            time.sleep(10)
        else:
            raise TimeoutError(f"VM {name} 未能在预期时间内启动。")
        inst = self.instances_client.get(project=self.project, zone=zone, instance=name)
        access = inst.network_interfaces[0].access_configs
        if not access or not access[0].nat_i_p:
            raise RuntimeError(f"未获取到实例 {name} 的外网 IP。")
        ip = access[0].nat_i_p
        ssh = self._ssh_connect(ip)
        self._upload_and_run(ssh, self.startup_script_path)
        self._wait_for_ready(name)

    def _upload_and_run(self, ssh: paramiko.SSHClient, local_script: str):
        """
        上传本地初始化脚本并在远端执行。
        """
        sftp = ssh.open_sftp()
        remote_path = '/root/worker_initial.sh'
        sftp.put(local_script, remote_path)
        sftp.chmod(remote_path, 0o755)
        sftp.close()
        # 执行脚本
        stdin, stdout, stderr = ssh.exec_command(f"bash {remote_path}")
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            error = stderr.read().decode()
            raise RuntimeError(f"初始化脚本执行失败: {error}")
        self.logger.info("初始化脚本执行成功。")

    def _ssh_connect(self, ip: str,  max_retries: int = 5, retry_interval: int = 10):
        """
        建立 SSH 连接并切换到 root。
        """
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(f"尝试 SSH 连接到 {ip}（第 {attempt}/{max_retries} 次）")
                ssh.connect(hostname=ip,
                            username='root',
                            key_filename='./config/kube-master-1-key',
                            timeout=20)
                self.logger.info(f"SSH 连接成功: {ip}")
                break
            except Exception as e:
                self.logger.warning(f"第 {attempt} 次连接失败: {e}")
                if attempt == max_retries:
                    raise RuntimeError(f"无法通过 SSH 连接到 {ip}，达到最大重试次数")
                time.sleep(retry_interval)
        # 设置 root 密码
        ssh.exec_command("echo 'root:123456' | sudo chpasswd")
        # 切换到 root
        shell = ssh.invoke_shell()
        shell.send("sudo su -\n")
        time.sleep(1)
        shell.send("123456\n")
        time.sleep(1)
        return ssh

    def _wait_for_ready(self, node_name: str, timeout: int = 300, interval: int = 10):
        start = time.time()
        while time.time() - start < timeout:
            if node_name in self.cluster_monitor.get_node_internal_ips():
                self.logger.info(f"节点 {node_name} Ready。")
                return
            time.sleep(interval)
        raise TimeoutError(f"等待节点 {node_name} Ready 超时")

    def delete_node(self, node_name: str, location: str, grace_period: int = 30):
        # 1. 安全退出集群
        self.cluster_monitor.drain_node(node_name)
        # 2. delete node from cluster
        try:
            self.cluster_monitor.core_v1.delete_node(node_name)
            self.logger.info(f"从集群中删除节点Deleted Kubernetes Node object {node_name}")
        except Exception as e:
            self.logger.warning(f"Failed to delete Node object {node_name}: {e}")
        # 3. 在 GCP 中查找并删除实例
        deleted = False
        agg = self.instances_client.aggregated_list(project=self.project)
        for zone_url, scoped_list in agg:
            instances = scoped_list.instances
            if not instances:
                continue
            zone = zone_url.split('/')[-1]
            for inst in instances:
                if inst.name == node_name:
                    self.logger.info(f"Deleting VM {node_name} in zone {zone}")
                    op = self.instances_client.delete(
                        project=self.project,
                        zone=zone,
                        instance=node_name
                    )
                    op.result()
                    deleted = True
                    break
            if deleted:
                break
        if not deleted:
            self.logger.warning(f"实例删除时没有找到Instance {node_name} not found in any zone")
            return False
        # 等待删除
        self._wait_for_deletion(node_name, zone, timeout=180)
        return True

    def _wait_for_deletion(self, node_name: str, zone: str, timeout: int = 300):
        start = time.time()
        while time.time() - start < timeout:
            try:
                # 调用 get，如果资源尚未删除则不会抛 404
                self.instances_client.get(project=self.project,
                                          zone=zone,
                                          instance=node_name)
                self.logger.debug(f"VM {node_name} still exists in zone {zone}, retrying...")
                time.sleep(5)
            except NotFound as e:
                if e.code == 404:
                    self.logger.info(f"实例 {node_name} 在 zone {zone} 已确认删除")
                    return
                else:
                    # 其它 HTTP 错误
                    self.logger.error(f"Error checking deletion of {node_name}: {e}")
                    raise
        raise TimeoutError(f"等待实例 {node_name} 在 zone {zone} 删除超时")

if __name__== "__main__":
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./config/single-cloud-ylxq-ed1608c43bb4.json"
    vm_manager = VMManager()
    # vm_manager.create_node(name="test-node", location="us-west1", machine_type="e2-standard-2")
    if vm_manager.delete_node(node_name="test-node", location="us-west1"):
        print("节点删除成功")
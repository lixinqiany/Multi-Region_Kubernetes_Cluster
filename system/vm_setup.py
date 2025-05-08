"""
调用gcp的module来创建实验环境
 - 虚拟机
 - 加入cluster
 - 前提：master已经存在
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from gcp.VMManager import VMManager
import os

def main():
    node_nums = 6
    name_tem = "node-"
    node_names = [f"{name_tem}{x+1}" for x in range(node_nums)]
    # use multiple threads to create nodes
    region = "australia-southeast1"
    machine_type = "e2-standard-8"
    vm_manager = VMManager()

    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_node = {
            executor.submit(vm_manager.create_node, name, region, machine_type,20): name
            for name in node_names
        }

        for future in as_completed(future_to_node):
            name = future_to_node[future]
            try:
                future.result()
                print(f"✅ 节点 {name} 创建完成")
            except Exception as exc:
                print(f"❌ 节点 {name} 创建失败: {exc}")


if __name__ == '__main__':
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./config/single-cloud-ylxq-ed1608c43bb4.json"
    main()
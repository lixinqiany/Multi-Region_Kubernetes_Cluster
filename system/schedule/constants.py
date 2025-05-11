"""
调度器常量与全局参数
"""
# 集群硬性上限
MAX_WORKER_NODES: int = 6
MAX_TOTAL_VCPU: int = 32           # 包含 master

# 模拟退火参数
SA_INITIAL_TEMPERATURE: float = 0.15
SA_MIN_TEMPERATURE: float = 0.01
SA_COOLING_RATE: float = 0.9
SA_MAX_ITER: int = 50              # 单 Pod/小批次足够；可按需调整

# 区域多样性惩罚
SINGLE_REGION_PENALTY: float = 0.12  # 美元/小时，按需调节

# 定价数据位置
REGION_PRICE_FILE = "./data/region_machine_prices.json"
MACHINE_TYPE_FILE = "./data/machine_types.json"

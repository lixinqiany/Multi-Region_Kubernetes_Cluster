"""
从 JSON 文件读取 GCP 各区域机型与价格 (按需)。
"""
import json
from pathlib import Path
from typing import Dict, List

from .constants import REGION_PRICE_FILE, MACHINE_TYPE_FILE
from .model import MachineType


class PricingLoader:
    """加载并缓存价格映射"""

    def __init__(self):
        self._machines: Dict[str, List[MachineType]] = {}
        self._load()

    # ------------------------------ public API ------------------------------ #

    def machines_in_region(self, region: str) -> List[MachineType]:
        """返回指定区域可用机型列表"""
        return self._machines.get(region, [])

    # ----------------------------- internal --------------------------------- #
    def _load(self):
        prices: dict = json.loads(Path(REGION_PRICE_FILE).read_text())
        mts: dict = json.loads(Path(MACHINE_TYPE_FILE).read_text())

        for region, mlist in mts.items():
            for mi in mlist:
                mt_name = mi["name"]
                price = prices.get(region, {}) \
                              .get("OnDemand", {}) \
                              .get(mt_name)
                if price is None:
                    # 无对应价格，忽略该机型
                    continue
                mt = MachineType(
                    name=mt_name,
                    vcpus=int(mi["vcpus"]),
                    mem_gib=float(mi["mem_gib"]),
                    price=float(price),
                    region=region
                )
                self._machines.setdefault(region, []).append(mt)

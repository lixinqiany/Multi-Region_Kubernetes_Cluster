"""
data_loader.py
~~~~~~~~~~~~~~
JSON → Python 结构化工具。
"""

from __future__ import annotations
from pathlib import Path
import json
from typing import Dict, Tuple


def load_price_map(json_path: str | Path) -> Dict[str, Dict[str, float]]:
    """
    返回结构：
        {region: {machine_type: on_demand_price}}
    """
    return json.loads(Path(json_path).read_text(encoding="utf-8"))


def load_spec_map(json_path: str | Path) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """
    返回结构：
        {region: {machine_type: (vCPU, Gi)}}
    """
    raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for region, lst in raw.items():
        out[region] = {item["name"]: (item["vcpus"], item["mem_gib"])
                       for item in lst}
    return out

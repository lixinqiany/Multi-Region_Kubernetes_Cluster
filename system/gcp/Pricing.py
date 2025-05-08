"""
gcp_pricing_catalog.py

使用 Cloud Billing Catalog API 获取 Compute Engine VM 定价信息，
并过滤掉 ARM 架构（T2A 系列）机型，仅保留 Intel x86（包括 AMD）机型。

数据来源：
  • Cloud Billing Catalog API:
    https://cloud.google.com/billing/docs/apis/catalog

示例用法：
  client = GCPBillingCatalogClient("path/to/sa-key.json")
  pricing_map = client.get_compute_engine_pricing()
  # 查看 us-central1 下的所有机型与定价
  for mt, price in pricing_map.get("us-central1", {}).items():
      print(f"{mt}: ${price:.4f}/hour")
"""
import os, logging, json
from google.cloud import billing_v1, compute_v1


class PricingClient:

    # Compute Engine 服务在 Catalog API 中的标识
    COMPUTE_ENGINE_SERVICE_NAME = "services/6F81-5844-456A"
    project_id = "single-cloud-ylxq"
    pricing_path = "../data/gcp/pricing_map.json"
    machine_types_path = "../data/gcp/machine_types.json"
    region_machine_price_path = "../data/gcp/region_machine_prices.json"

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.client = billing_v1.CloudCatalogClient()
        self.compute_client = compute_v1.MachineTypesClient()

        self.logger.info("谷歌账单和机器类型池服务初始化 Initialized CloudCatalogClient")

    def list_compute_skus(self, page_size: int = 500):
        """
        分页获取 Compute Engine 下的所有 SKU，并做以下过滤：
          1. resource_family == "Compute"
          2. usage_type in ("OnDemand", "Preemptible")
          3. description 不包含 "Custom"
        返回过滤后的 SKU 对象列表。
        """
        skus = []
        # .list_skus 返回一个迭代器，自动处理分页
        for sku in self.client.list_skus(parent=self.COMPUTE_ENGINE_SERVICE_NAME):
            cat = sku.category
            if cat.resource_family != "Compute":
                continue  # only interested in Compute Engine Instance
            if cat.usage_type not in ("OnDemand", "Preemptible"):
                continue  # only interested in OnDemand and Preemptible
            desc = (sku.description or "")
            if ("Custom" in desc or "Reserved" in desc or "Sole Tenancy" in desc or "GPU" in desc or "NVIDIA" in desc or
                    "DWS" in desc or "Optimized" in desc):
                continue
            skus.append(sku)
        return skus

    def get_compute_engine_pricing(self) :
        """
        提取 Compute Engine VM 的 CPU 和内存单价，并按区域分组：
          region -> {
            machine_type: {
              "cpu_price": USD/core·hour,
              "memory_price": USD/GiB·hour
            }
          }
        """
        # —— 新增：缓存判断 —— #
        if os.path.exists(self.pricing_path):
            self.logger.info(f"Cache found at {self.pricing_path}, loading pricing map from file")
            with open(self.pricing_path, "r", encoding="utf-8") as f:
                return json.load(f)

        skus = self.list_compute_skus()
        # 临时结构： region -> billing_type -> mt -> {"cpu_price", "memory_price"}
        pricing_map = {}
        for sku in skus:
            usage_type = sku.category.usage_type
            # 确保结构存在
            for region in sku.service_regions or []:
                pricing_map.setdefault(region, {}) \
                    .setdefault(usage_type, {})

            # 从 pricing_info[0].pricing_expression.tiered_rates[0] 获取单价
            expr = sku.pricing_info[0].pricing_expression
            if not expr.tiered_rates:
                continue
            rate0 = expr.tiered_rates[0]
            unit_price = rate0.unit_price.units + rate0.unit_price.nanos / 1e9

            # 从描述中提取 machine_type
            desc = sku.description or ""
            desc_l = desc.lower()

            # 只处理 Core running 与 Ram running 两类 SKU
            if "core running" in desc_l:
                part = "cpu_price"
            elif "ram running" in desc_l:
                part = "memory_price"
            else:
                # 过滤掉其它非 CPU/内存 计费项
                continue
            mt = None
            for token in desc.split():
                tl = token.lower()
                # 匹配常见机型前缀
                if tl.startswith(("n1", "n2", "n2d", "n3", "n3d", "n4",
                                  "e2",
                                  "c2", "c2d", "c3", "c3d", "c4", "c4a",
                                  "m3", "m2",
                                  "h3")):
                    mt = tl
                    break
            if not mt:
                continue

            # 过滤 ARM 架构（t2a- 或 描述含 ARM）
            if mt.startswith("t2a-") or "arm" in desc.lower():
                continue

            # 将价格填入对应 region & usage_type & machine_type
            for region in sku.service_regions or []:
                entry = pricing_map[region][usage_type] \
                    .setdefault(mt, {"cpu_price": 0.0, "memory_price": 0.0})
                entry[part] = unit_price
        self.logger.info("gcp可用域机器定价数据获取完毕")
        return pricing_map

    def get_and_write_pricing(self, filepath: str = "../data/gcp/pricing_map.json"):
        """
        调用 get_compute_engine_pricing，并将结果写到指定文件。
        默认路径：data/pricing_map.json
        """
        filepath = self.pricing_path
        pricing = self.get_compute_engine_pricing()

        # 确保目录存在
        directory = os.path.dirname(filepath)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            self.logger.info(f"Created directory: {directory}")

        # 写入 JSON
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(pricing, f, indent=2, ensure_ascii=False)
        self.logger.info(f"定价数据写入Wrote pricing map to {filepath}")

    def list_region_machine_types(self):
        """
        使用 google.cloud.compute_v1 列出各区域可用机型及其规格：
          region -> [
            {"name": "e2-standard-4", "vcpus": 4, "mem_gib": 16.0}, ...
          ]
        排除名称中含 'custom' 的机型。
        """
        #self.logger.info("Fetching machine types via compute_v1 API …")
        # 1. 检查缓存
        cache_path = self.machine_types_path
        if os.path.exists(cache_path):
            self.logger.info(f"Cache found at {cache_path}, loading region machine types from file")
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

        result = {}

        req = compute_v1.types.AggregatedListMachineTypesRequest(project=self.project_id)
        agg = self.compute_client.aggregated_list(request=req)
        for zone_scope, resp in agg:
            # zone_scope 格式: "zones/{zone}"
            if resp.machine_types:
                zone = zone_scope.split("/")[-1]  # e.g. "us-central1-a"
                region = "-".join(zone.split("-")[:-1])  # e.g. "us-central1"
                for mt in resp.machine_types:
                    name = mt.name  # e2-standard-4
                    if "custom" in name:
                        continue
                    vcpus = mt.guest_cpus
                    mem_gib = mt.memory_mb / 1024.0
                    result.setdefault(region, []).append({
                        "name": name,
                        "vcpus": vcpus,
                        "mem_gib": mem_gib
                    })

        self.logger.info(f"Collected machine types for {len(result)} regions")
        return result

    def get_and_write_region_machine_types(self):
        """
                调用 list_region_machine_types 获取各区域机型规格，并写入指定文件。
                默认路径：../data/gcp/region_machine_types.json
                """
        # 获取区域-机型规格
        region_types = self.list_region_machine_types()

        # 确保目录存在
        directory = os.path.dirname(self.machine_types_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            self.logger.info(f"Created directory: {directory}")

        # 写入 JSON
        with open(self.machine_types_path, "w", encoding="utf-8") as f:
            json.dump(region_types, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Wrote region machine types to {self.machine_types_path}")


    def get_region_machine_type_prices(self):
        """
        组合单价与机型规格，计算整机小时价格：
          region -> {
            "OnDemand":   { mt_name: total_price },
            "Preemptible":{ mt_name: total_price }
          }
        """
        unit_map = self.get_compute_engine_pricing()
        specs = self.list_region_machine_types()

        final = {}
        for region, mts in specs.items():
            if region not in unit_map:
                continue
            for usage in ("OnDemand", "Preemptible"):
                if usage not in unit_map[region]:
                    continue
                for mt in mts:
                    family = mt["name"].split("-")[0]
                    unit_prices = unit_map[region][usage].get(family)
                    if not unit_prices:
                        continue
                    cpu_p = unit_prices["cpu_price"]
                    mem_p = unit_prices["memory_price"]
                    total = mt["vcpus"] * cpu_p + mt["mem_gib"] * mem_p
                    final.setdefault(region, {}) \
                        .setdefault(usage, {})[mt["name"]] = round(total, 6)

        self.logger.info("Computed full machine-type pricing map")
        return final

    def get_and_write_region_machine_type_prices(self):
        """
        如果缓存存在，则直接从文件读取 region-machine-type 价格并返回；
        否则调用 get_region_machine_type_prices() 计算、写入缓存并返回。
        """
        # 1. 如果缓存文件存在，直接加载
        if os.path.exists(self.region_machine_price_path):
            self.logger.info(f"Cache found at {self.region_machine_price_path}, loading region-machine-type prices from file")
            with open(self.region_machine_price_path, "r", encoding="utf-8") as f:
                return json.load(f)

        # 2. 否则计算并写入
        self.logger.info("Cache not found, computing region-machine-type prices …")
        data = self.get_region_machine_type_prices()

        # 确保目录存在
        directory = os.path.dirname(self.region_machine_price_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            self.logger.info(f"Created directory for cache: {directory}")

        # 写入缓存
        with open(self.region_machine_price_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Wrote region-machine-type prices cache to {self.region_machine_price_path}")

        return data


if __name__ == "__main__":
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "../config/single-cloud-ylxq-ed1608c43bb4.json"
    client = PricingClient()
    client.get_and_write_region_machine_types()
    client.get_and_write_region_machine_type_prices()

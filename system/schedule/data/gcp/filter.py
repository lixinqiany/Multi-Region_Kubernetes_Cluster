import json
from pathlib import Path
from shutil import copyfile

# ───── 配置 ─────
MIN_VCPU = 8                     # 仅保留 vcpu > 8 的机型
MT_PATH  = Path("machine_types.json")
PR_PATH  = Path("region_machine_prices.json")
NAME_BLACKLIST = ["n2d", "micro", "medium", "small", "c2d"]
REGION_BLACKLIST_SUBSTR = "us-central1"  # 过滤包含此子串的 region

# ───── 0. 读取原始数据 ─────
with MT_PATH.open(encoding="utf-8") as f:
    mt_data = json.load(f)
with PR_PATH.open(encoding="utf-8") as f:
    price_data = json.load(f)

# ───── 1. 过滤掉不想要的 region ─────
# 先从字典中移除所有包含 REGION_BLACKLIST_SUBSTR 的 key
for region in list(mt_data.keys()):
    if REGION_BLACKLIST_SUBSTR in region:
        mt_data.pop(region, None)
for region in list(price_data.keys()):
    if REGION_BLACKLIST_SUBSTR in region:
        price_data.pop(region, None)

# ───── 2. 过滤 machine_types.json ─────
retained_mt_names = set()
for region, lst in mt_data.items():
    # 仅保留 vcpus > MIN_VCPU，且名称不包含黑名单关键字
    new_lst = [
        item for item in lst
        if item["vcpus"] <= MIN_VCPU
           and all(bl not in item["name"] for bl in NAME_BLACKLIST)
    ]
    mt_data[region] = new_lst
    retained_mt_names.update(item["name"] for item in new_lst)

print(f"[INFO] after filtering, {len(retained_mt_names)} machine-types retained")

# ───── 3. 过滤 region_machine_prices.json ─────
for region, kinds in price_data.items():
    for price_cat in ("OnDemand", "Preemptible"):
        if price_cat not in kinds:
            continue
        before = len(kinds[price_cat])
        # 仅保留名称在 retained_mt_names 中的机型
        kinds[price_cat] = {
            mt: price
            for mt, price in kinds[price_cat].items()
            if mt in retained_mt_names
        }
        print(f"[{region}] {price_cat}: {before} → {len(kinds[price_cat])}")

# ───── 4. 备份并写回 ─────
copyfile(MT_PATH, MT_PATH.with_suffix(".json.bak"))
copyfile(PR_PATH, PR_PATH.with_suffix(".json.bak"))

with MT_PATH.open("w", encoding="utf-8") as f:
    json.dump(mt_data, f, indent=2, ensure_ascii=False)
with PR_PATH.open("w", encoding="utf-8") as f:
    json.dump(price_data, f, indent=2, ensure_ascii=False)

print("[DONE] files overwritten (backup saved as *.bak)")

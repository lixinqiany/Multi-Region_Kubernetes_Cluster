"""
scheduler.py
~~~~~~~~~~~~
调度主模块：定期循环
  • 采集集群状态 → ResourceModel
  • 调用 Optimizer 生成新方案
  • Diff 现状 ←→ 新方案，决定扩容 / 缩容 / Pod 迁移
目前仅实现 **骨架**：
  – 节点扩缩容：调用 VMManager.create_node / delete_node
  – Pod 迁移：留 TODO（需结合业务，或采用 K8s Default Descheduler）
"""
from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor

from kubernetes.client.rest import ApiException
import concurrent
import time, os, json
import logging
from typing import List, Dict
from pathlib import Path
from sa_optimizer import SAOptimizer, energy
from contourpy.types import point_dtype
from kubernetes import client
from ClusterMonitor import ClusterMonitor
from VMManager import VMManager

from cluster_state import snapshot_cluster
from optimizer_interface import BaseOptimizer, NoOpOptimizer
from resource_model import ResourceModel
from resource_types import Pod, Node
from rfsa_optimizer import RFSAOptimizer
import csv, datetime

class Scheduler:
    """
    Parameters
    ----------
    optimizer : BaseOptimizer
        调度算法实例（默认 NoOp，占位）。
    interval_sec : int
        主循环间隔（秒）。
    """

    def __init__(self,
                 optimizer: BaseOptimizer | None = None,
                 interval_sec: int = 120,
                 full_threshold=0.95, cooldown_sec=240):
        self.monitor = ClusterMonitor()
        self.vm = VMManager()
        self.optimizer = optimizer or NoOpOptimizer()
        self.interval_sec = interval_sec
        self.logger = logging.getLogger("SAScheduler")
        self.logger.setLevel(logging.INFO)
        self.last_full_ts = 0
        self.full_threshold = full_threshold
        self.cooldown = cooldown_sec
        self.cycle_id = 0
        self.hist_path = Path(__file__).parent / "data/schedule_history.csv"
        # ✨ 互斥锁：调度 & Consolidator
        self.op_lock = threading.Lock()

        self.creation_block_sec = 150
        self.last_node_create_ts = 0  # 上次新建节点时间戳
        # 启动 Consolidator 守护线程
        threading.Thread(target=self._consolidate_loop,
                         daemon=True).start()

    # ──────────────────────────────────────────────
    # 主循环
    # ──────────────────────────────────────────────
    def run_forever(self):
        """
        while-true 调度循环；Ctrl+C 可退出。
        """
        cycle_id = 0
        self.logger.info("SA Scheduler started. interval=%ss", self.interval_sec)
        while True:
            start = time.time()
            try:
                with self.op_lock:          # ✨ 与 Consolidator 互斥
                    self._run_once(cycle_id)
                    time.sleep(10)
            except Exception as exc:     # noqa
                self.logger.exception("scheduler run_once failed: %s", exc)

            # 维持固定间隔
            elapsed = time.time() - start
            cycle_id += 1
            time.sleep(max(0, self.interval_sec - elapsed))

    # ──────────────────────────────────────────────
    # 单轮调度
    # ──────────────────────────────────────────────
    def _run_once(self,cycle_id: int):
        # 1. 采集状态
        cur_plan = snapshot_cluster(self.monitor)
        pending_pods = self._fetch_pending_list()
        if not pending_pods:
            self.logger.info("no pending pods, skip this cycle")
            return

        # 2. 算法优化
        # — 1) 先跑一次 SA-增量 —
        inc_plan, still_inc = self.optimizer.optimize(cur_plan, pending_pods, mode="incremental")
        self._reuse_nodes(cur_plan, inc_plan, spec_gap=0.05, price_gap=0.05)
        E_inc = energy(inc_plan)
        # —— 判断是否需要全量 —— #
        do_full = False
        if time.time() - self.last_full_ts >= self.cooldown:
            # 2) 再跑一次 SA-全量
            full_plan, still_full = self.optimizer.optimize(cur_plan, pending_pods, mode="full")
            self._reuse_nodes(cur_plan, full_plan, spec_gap=0.05, price_gap=0.05)
            E_full = energy(full_plan)

            # 3) 比较能量
            if E_full / (E_inc + 1e-8) <= self.full_threshold:
                do_full = True
                chosen_plan, still = full_plan, still_full
            else:
                chosen_plan, still = inc_plan, still_inc
        else:
            chosen_plan, still = inc_plan, still_inc

        # ✨ 后处理：合并等价节点
        self._pack_small_nodes(
            chosen_plan,
            spec_map=self.optimizer.seed.spec_map,
            price_map=self.optimizer.seed.price_map
        )
        # 3. 执行 plan 差异
        self._apply_plan(cur_plan, chosen_plan)

        if do_full:
            self.last_full_ts = time.time()

        if still:
            self.logger.warning("unscheduled pod(s): %s",
                                [p.full_name for p in still])
        self.logger.info("schedule cycle finished | mode=%s",
                         "FULL" if do_full else "INCREMENTAL")
        mode_str = "full" if do_full else "incremental"
        self._dump_history(chosen_plan, mode_str)
        self.cycle_id += 1

    def _pack_small_nodes(self, plan: ResourceModel,
                          spec_map: dict, price_map: dict):
        """
        对计划中新建的小规格节点 (is_existing=False) 进行“8C 打包”：
        1. 分组键：family + price_small
        2. 对组内节点按 usable_cpu 降序做贪心装箱 (bin_cap = 8-DEFAULT_OVERHEAD)
        3. 每个箱内若节点≥2 → 寻找同 family ≤8C、价格≤原价和 的机型
           • Region 选择：若箱内节点都同 Region→沿用；否则在箱内出现 Region 中选
             当前 plan 节点最少的
        """
        from random import randint

        bin_cap = 8  # 机器上限 (raw vCPU)

        # ------- 现有 Region 计数 -------
        region_hist: Dict[str, int] = {}
        for nd in plan.nodes.values():
            if nd.name != "master":
                region_hist[nd.region] = region_hist.get(nd.region, 0) + 1

        # ------- 以 (family, price) 分组 -------
        groups: Dict[tuple, list[Node]] = {}
        for nd in plan.nodes.values():
            if nd.name == "master":
                continue  # 仅处理 new 小节点
            parts = nd.machine_type.rsplit("-", 1)
            family = parts[0] if len(parts) == 2 and parts[1].isdigit() else nd.machine_type
            key = (family, nd.price)
            groups.setdefault(key, []).append(nd)

        # ------- 遍历分组 -------
        for (family, price_small), nodes in groups.items():
            if len(nodes) < 2:
                continue

            # ① usable_cpu 降序，便于装箱
            nodes.sort(key=lambda n: n.usable_cpu_cap, reverse=True)
            bins: list[list[Node]] = []

            # ② 贪心装箱 (Bin Packing)
            for nd in nodes:
                placed = False
                for b in bins:
                    cpu_used = sum(n.usable_cpu_cap for n in b)
                    if cpu_used + nd.usable_cpu_cap <= bin_cap - Node.DEFAULT_OVERHEAD_CPU:
                        b.append(nd)
                        placed = True
                        break
                if not placed:
                    bins.append([nd])

            # ③ 每个箱尝试合并
            for box in bins:
                if len(box) < 2:
                    continue

                cpu_sum = sum(n.usable_cpu_cap for n in box)
                mem_sum = sum(n.mem_cap for n in box)
                price_sum = price_small * len(box)
                regions_box = {n.region for n in box}

                # --- 选目标 Region ---
                if len(regions_box) == 1:
                    target_region = next(iter(regions_box))
                else:
                    target_region = min(regions_box,
                                        key=lambda r: region_hist.get(r, 0))

                # --- 在 family 找 ≤8C 机型 ---
                cand = []
                for mt, (vcpu, mem) in spec_map[target_region].items():
                    if vcpu > 8 or not mt.startswith(family + "-"):
                        continue
                    price_new = price_map.get(target_region, {}).get("OnDemand", {}).get(mt)
                    if price_new is None:
                        continue
                    if vcpu - Node.DEFAULT_OVERHEAD_CPU >= cpu_sum \
                            and mem >= mem_sum \
                            and price_new <= price_sum*1.1:
                        cand.append((vcpu, mem, price_new, mt))
                if not cand:
                    continue

                cand.sort(key=lambda t: (t[0], t[2]))  # 小 vcpu → 低价
                vcpu, mem, price_new, mt = cand[0]

                # --- 创建新节点并迁移 ---
                new_name = f"pack-{target_region}-{mt}-{randint(10000, 99999)}"
                new_nd = Node(new_name, target_region, mt, vcpu, mem,
                              price_new, is_existing=False)

                success = True
                moved: list[tuple[Node, Pod]] = []  # 记录已迁 Pod ⟶ 回滚用

                for nd in box:
                    for pod in list(nd.pods):
                        if not new_nd.can_fit(pod):
                            success = False
                            break
                        nd.rm_pod(pod)
                        new_nd.add_pod(pod)
                        plan.pod2node[pod.full_name] = new_name
                        moved.append((nd, pod))
                    if not success:
                        break

                if not success:
                    # ⟲ 回滚：把已迁 Pod 退回原节点
                    for old_nd, pod in moved:
                        new_nd.rm_pod(pod)
                        old_nd.add_pod(pod)
                        plan.pod2node[pod.full_name] = old_nd.name
                    continue  # 放弃当前箱，处理下一个箱

                # ---- 真正合并成功 ----
                plan.open_node(new_nd)
                for nd in box:
                    # ✨ 清零残留资源，避免断言
                    nd.cpu_used = nd.mem_used = 0
                    nd.pods.clear()
                    plan.close_node(nd)
                    region_hist[nd.region] -= 1
                region_hist[target_region] = region_hist.get(target_region, 0) + 1

    def _reuse_nodes(self, cur: ResourceModel, new: ResourceModel,
                     spec_gap=0.05, price_gap=0.05):
        """把 new 里 plan-node(is_existing=False) 尝试映射到
           cur 里已有节点(is_existing=True)。"""
        for nd_new in list(new.nodes.values()):
            if nd_new.is_existing:
                continue
            for nd_old in cur.nodes.values():
                if not nd_old.is_existing:
                    continue
                if abs(nd_new.cpu_cap - nd_old.cpu_cap) / nd_old.cpu_cap > spec_gap:
                    continue
                if abs(nd_new.mem_cap - nd_old.mem_cap) / nd_old.mem_cap > spec_gap:
                    continue
                if abs(nd_new.price - nd_old.price) / nd_old.price > price_gap:
                    continue
                # —— 替换 —— #
                for p_full, n_name in list(new.pod2node.items()):
                    if n_name == nd_new.name:
                        pod = self._find_pod_obj(p_full, new)
                        nd_old.add_pod(pod)
                        new.pod2node[p_full] = nd_old.name
                del new.nodes[nd_new.name]
                break

    @staticmethod
    def _find_pod_obj(full_name: str, plan: ResourceModel) -> Pod | None:
        nd_name = plan.pod2node.get(full_name)
        if nd_name:
            for p in plan.nodes[nd_name].pods:
                if p.full_name == full_name:
                    return p
        return None

    # ──────────────────────────────────────────────
    # 帮助：拉取 Pending Pod 列表
    # ──────────────────────────────────────────────
    def _fetch_pending_list(self) -> List[Pod]:
        plist = []
        resp = self.monitor.core_v1.list_pod_for_all_namespaces(
            field_selector="status.phase=Pending")
        for p in resp.items:
            # 只处理 custom-scheduling、default、未绑定的 Pod
            if p.spec.scheduler_name != "custom-scheduling":
                continue
            if p.metadata.namespace != "default":
                continue
            if p.spec.node_name:
                continue

            #pod=p
            # CPU / MEM 解析逻辑与 cluster_state 一致
            from cluster_state import _parse_cpu, _parse_mem
            # 计算请求
            cpu_req = 0.0
            mem_req = 0.0
            for c in p.spec.containers:
                reqs = c.resources.requests or {}
                cpu_req += _parse_cpu(reqs.get("cpu", "0"))
                mem_req += _parse_mem(reqs.get("memory", "0"))
            pod= Pod(p.metadata.name,
                p.metadata.namespace,
                cpu_req,
                mem_req,
                p.metadata.labels or {})
            pod.is_new = True
            plist.append(
                pod
            )
        return plist

    # ──────────────────────────────────────────────
    # 扩容 / 缩容 / Pod 迁移
    # ──────────────────────────────────────────────
    def _apply_plan(self,
                    old: ResourceModel,
                    new: ResourceModel):
        """
        Diff 两份 ResourceModel：
          • 节点差异 → VMManager 扩缩容
          • Pod 绑定差异 → TODO：驱逐 + rebind
        """
        NODE_INFO_PATH = Path(__file__).parent / "node_info.json"
        node_info = json.loads(NODE_INFO_PATH.read_text(encoding="utf-8"))
        old_nodes = set(old.nodes.keys())
        new_nodes = set(new.nodes.keys())

        pending_delete = old_nodes - new_nodes  # 先记录，稍后再删
        pending_create = new_nodes - old_nodes

        # ───────── ① 并发删除旧节点 ───────── #
        def _delete(node_name: str):
            region_guess = "-".join(node_name.split("-")[:-1])
            try:
                self.vm.delete_node(node_name, region_guess)
                self.logger.info("delete node %s", node_name)
                node_info.pop(node_name, None)
            except Exception as exc:
                self.logger.error("delete node %s failed: %s", node_name, exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(_delete, pending_delete))

        # ───────── ② 并发创建新节点 ───────── #
        def _create(node_name: str):
            nd = new.nodes[node_name]
            try:
                self._try_create_with_fallback(node_name, nd)
                node_info[node_name] = {
                    "machine_type": nd.machine_type,
                    "region": nd.region
                }
            except Exception as exc:
                self.logger.error("create node %s failed: %s", node_name, exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(_create, pending_create))

        # ───────── ③ 等新节点 Ready ───────── #
        for n in pending_create:
            if self.monitor.wait_node_ready(n, timeout=300):
                self.logger.info("node %s ready", n)
            else:
                self.logger.warning("node %s not ready within timeout", n)

        # ───────── ④ 绑定仍 Pending 的实验 Pod ───────── #
        for full, tgt_node in new.pod2node.items():
            # full 可能是  namespace/podName  或  podName
            if "/" in full:
                ns, name = full.split("/", 1)
            else:
                ns, name = "default", full

            # 若位置未变 → 跳过
            if full in old.pod2node and old.pod2node[full] == tgt_node:
                continue

            # 尝试读取 Pod；404/410 可能已被 ReplicaSet 删除
            try:
                pod_obj = self.monitor.core_v1.read_namespaced_pod(name, ns)
            except ApiException as exc:
                if exc.status in (404, 410):
                    self.logger.info("pod %s not found, skip", full)
                    continue
                self.logger.error("read pod %s failed: %s", full, exc)
                continue

            # 仅当 Pod 仍 Pending 且尚未绑定时处理
            if pod_obj.status.phase == "Pending" and pod_obj.spec.node_name is None:
                try:
                    self.monitor.bind_pod(name, ns, tgt_node)
                    self.logger.info("bind Pending pod %s → %s", full, tgt_node)
                except Exception as exc:
                    self.logger.error("bind failed for %s: %s", full, exc)

        # -- 4️⃣  持久化 node_info  --------------------------------------------
        NODE_INFO_PATH.write_text(json.dumps(node_info, indent=2), "utf-8")

        # -- 5️⃣  摘要日志  -----------------------------------------------------
        self.logger.info("apply diff done: +%d nodes, -%d nodes",
                         len(pending_create), len(pending_delete))
        if pending_create:
            self.last_node_create_ts = time.time()

    # -------------------------------------------------
    def _try_create_with_fallback(self, vm_name: str, nd):
        """
        尝试在原 Region 创建 VM；若资源池耗尽或配额不足，
        自动在 “同单价” 的其它 Region/Zone 重试。
        """
        try:
            self.vm.create_node(vm_name, nd.region, nd.machine_type)
            self.logger.info("create node %s (%s/%s)", vm_name, nd.region, nd.machine_type)
            return
        except Exception as exc:
            msg = str(exc)
            if "ZONE_RESOURCE_POOL_EXHAUSTED" not in msg \
                    and "QUOTA_EXCEEDED" not in msg:
                raise  # 其它异常直接抛出

            self.logger.warning("region %s unavailable (%s), searching fallback...",
                                nd.region, msg.split(":")[0])

        # -------- 1) 收集同单价候选 --------
        price_map = self.optimizer.seed.price_map  # RFSA price_map
        spec_map = self.optimizer.seed.spec_map

        price_orig = nd.price
        family = nd.machine_type.split("-")[0]  # e.g. n1, n2d, n4 ...
        # 遍历所有 region，找 price 相同 && 机型存在
        cand = []
        for region, mts in price_map.items():
            if region == nd.region:
                continue
            price = mts.get("OnDemand", {}).get(nd.machine_type)
            if price is not None and abs(price - price_orig) < 1e-6:
                cand.append(region)

        if not cand:
            raise RuntimeError("no region with same price for fallback")

        # -------- 2) 逐个 Region 尝试 --------
        for region in cand:
            alt_vm_name = f"{vm_name}"
            try:
                self.vm.create_node(alt_vm_name, region, nd.machine_type)
                self.logger.info("fallback create %s (%s/%s) succeeded",
                                 alt_vm_name, region, nd.machine_type)
                # 更新节点对象属性以便后续 apply_plan
                nd.name = alt_vm_name
                nd.region = region
                return
            except Exception as exc:
                self.logger.warning("fallback region %s failed: %s", region, exc)

        raise RuntimeError("all fallback regions exhausted for " + vm_name)

    # -------------------------------------------------
    def _energy_parts(self, plan: ResourceModel) -> tuple[float, float, float, float]:
        """返回 (energy, cost, idle, conc) 组合指标。"""
        cost = sum(nd.price for nd in plan.nodes.values() if nd.name != "master")
        idle = sum((nd.usable_cpu_cap - nd.cpu_used) / nd.usable_cpu_cap
                   for nd in plan.nodes.values()
                   if nd.name != "master" and nd.usable_cpu_cap > 0)

        reg_hist: Dict[str, List[str]] = {}
        for nd in plan.nodes.values():
            if nd.name == "master":
                continue
            reg_hist.setdefault(nd.region, []).append(nd.name)
        total = len([n for n in plan.nodes if n != "master"])
        conc = sum((len(v) / total) ** 2 for v in reg_hist.values()) if total else 1

        E = energy(plan)  # 引自 sa_optimizer.energy
        return E, cost, idle, conc

    # -------------------------------------------------
    def _dump_history(self, plan: ResourceModel, mode: str):
        E, cost, idle, conc = self._energy_parts(plan)
        nodes_desc = ";".join(f"{nd.region}|{nd.machine_type}|{nd.price}|{nd.name}"
                              for nd in plan.nodes.values() if nd.name != "master")
        # ② 新增：节点-Pod 映射
        mapping = []
        for nd in plan.nodes.values():
            if nd.name == "master":
                continue
            pods = "|".join(p.full_name for p in nd.pods)
            mapping.append(f"{nd.name}:[{pods}]")
        nodes_pods = ";".join(mapping)

        write_header = not self.hist_path.exists()
        with self.hist_path.open("a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["ts", "cycle_id", "mode", "energy",
                            "cost", "idle_ratio", "conc", "node_cnt", "nodes","nodes_pods"])
            w.writerow([
                datetime.datetime.utcnow().isoformat(),
                self.cycle_id, mode, f"{E:.4f}",
                f"{cost:.4f}", f"{idle:.4f}", f"{conc:.4f}",
                len([n for n in plan.nodes if n != "master"]),
                nodes_desc,
                nodes_pods
            ])

    # ───────── Consolidator 线程 ─────────
    def _consolidate_loop(self,
                          sleep_sec: int = 240,
                          low_thr: float = 0.45):
        while True:
            try:
                with self.op_lock:  # ✨ 互斥
                    if time.time() - self.last_node_create_ts < self.creation_block_sec:
                        continue  # 直接跳过本轮 consolidate
                    plan = snapshot_cluster(self.monitor)
                    for nd in list(plan.nodes.values()):
                        to_remove = [nd for nd in plan.nodes.values()
                                     if nd.name not in ("master", "node-1")
                                     and self.monitor.get_node_cpu_util(nd.name) < low_thr]

                    if not to_remove:
                        continue

                    removed_names = []

                    def _del(nd):
                        return nd.name if self._close_idle_node(nd) else None

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        for r in pool.map(_del, to_remove):
                            if r:
                                removed_names.append(r)

                    #removed_names = []
                    #for nd in to_remove:
                    #    if self._close_idle_node(nd):
                    #        removed_names.append(nd.name)

                    if removed_names:
                        # 统一记录一次关机后的集群状态
                        plan_now = snapshot_cluster(self.monitor)
                        self._dump_history(plan_now, mode="consolidate")
                        self.logger.info("[consolidate] closed nodes: %s",
                                         ",".join(removed_names))
            except Exception as exc:
                self.logger.exception("[consolidate] %s", exc)
            finally:
                time.sleep(sleep_sec)

    def _close_idle_node(self, nd):
        self.logger.info("[consolidate] try close %s util=%.2f%%",
                         nd.name, nd.util_ratio * 100)
        region_guess = "-".join(nd.name.split("-")[:-1])
        try:
            self.vm.delete_node(nd.name, region_guess)
            self.logger.info("[consolidate] deleted node %s", nd.name)
            return True
        except Exception as exc:
            self.logger.error("[consolidate] delete %s failed: %s", nd.name, exc)
            return False


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./config/single-cloud-ylxq-ed1608c43bb4.json"
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from sa_optimizer import SAOptimizer
    optimizer = RFSAOptimizer(
        price_json="data/gcp/region_machine_prices.json",
        spec_json="data/gcp/machine_types.json"
    )
    sa = SAOptimizer(seed_optimizer=optimizer,
                     n_iter=200,  # 每个温度邻域尝试次数
                     T0=60, Tmin=1, alpha=0.9)

    sched = Scheduler(optimizer=sa, interval_sec=120)   # 每 2 分钟调度一次
    sched.run_forever()

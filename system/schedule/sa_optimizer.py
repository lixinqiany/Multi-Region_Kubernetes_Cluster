"""
sa_optimizer.py (v2)
~~~~~~~~~~~~~~~~~~~~
Simulated Annealing (SA) 优化器 —— 以 RFSA 初始解为起点，在
**硬约束** (≤6 个 worker 节点、总 vCPU ≤30) 下做全局搜索。

• 若 RFSA 已达到上限，SA 将仅在现有节点上重排；
  否则可继续开新节点，但开前先验算约束。
• 若邻域动作违反约束，则该邻域被视为无效并丢弃。

依赖：
  - optimizer_interface.BaseOptimizer
  - resource_model / resource_types
  - rfsa_optimizer.RFSAOptimizer  作为 seed
"""

from __future__ import annotations
import math, random
from typing import List, Tuple, Dict

from optimizer_interface import BaseOptimizer
from resource_model import ResourceModel
from resource_types import Pod, Node
from rfsa_optimizer import RFSAOptimizer      # 种子 Optimizer

# ─────────────────────────────────────────────── #
# 集群硬约束
# ─────────────────────────────────────────────── #
MAX_WORKER_NODES = 6        # 不含 master
MAX_CLUSTER_CPU  = 30       # worker vCPU 总量上限


# ─────────────────────────────────────────────── #
# 能量函数（成本主导 + 闲置率 + 区域均衡）
# ─────────────────────────────────────────────── #
def energy(plan: ResourceModel,
           w_cost=1.0, w_idle=0.5, w_region=0.4, w_nodes=0.6) -> float:
    cost = sum(nd.price for nd in plan.nodes.values() if nd.name != "master" and nd.name!="node-1")
    idle = sum((nd.cpu_cap - nd.cpu_used) / nd.cpu_cap
               for nd in plan.nodes.values() if nd.name != "master" and nd.name!="node-1")

    # 区域集中度
    reg_hist: Dict[str, List[str]] = {}
    for nd in plan.nodes.values():
        reg_hist.setdefault(nd.region, []).append(nd.name)
    total = len([n for n in plan.nodes if n != "master" and n != "node-1"])
    conc  = sum((len(v)/total) ** 2 for v in reg_hist.values()) if total else 1

    return w_cost*cost + w_idle*idle + w_region*conc + w_nodes*total


# ─────────────────────────────────────────────── #
# SA Optimizer
# ─────────────────────────────────────────────── #
class SAOptimizer(BaseOptimizer):
    """
    Simulated Annealing with HARD CONSTRAINTS.

    Parameters
    ----------
    seed_optimizer : BaseOptimizer
        负责生成初始可行解（推荐 RFSAOptimizer）。
    n_iter : int
        每个温度尝试的邻域次数。
    T0/Tmin/alpha : float
        初始温度 / 最低温度 / 降温系数。
    """

    def __init__(self,
                 seed_optimizer: BaseOptimizer,
                 n_iter: int = 600,
                 T0: float = 60,
                 Tmin: float = 1.0,
                 alpha: float = 0.88):
        self.seed = seed_optimizer
        self.n_iter = n_iter
        self.T0, self.Tmin, self.alpha = T0, Tmin, alpha

    # ========= 公开接口 ========= #
    def _normalize(self, plan: ResourceModel):
        """
        保证 plan.pod2node 的每个 pod 只存在于对应节点的 nd.pods 里。
        """
        for full_name, node_name in list(plan.pod2node.items()):
            for nd in plan.nodes.values():
                if nd.name != node_name:
                    # 强制从非目标节点移除同名 Pod
                    nd._pods = [p for p in nd.pods if p.full_name != full_name]
        # （cpu_used/mem_used 与 pods 列表保持一致，后面可补充资源重算）

    def optimize(self,
                 current: ResourceModel,
                 pending: List[Pod],
                 mode:str="incremental") -> Tuple[ResourceModel, List[Pod]]:
        # (1) 先用 RFSA 拿到可行初始解
        self.inc_mode = (mode == "incremental")
        plan, still = self.seed.optimize(current, pending)

        best, best_E = plan.clone(), energy(plan)

        T = self.T0
        while T > self.Tmin:
            for _ in range(self.n_iter):
                nbr = self._neighbor(plan, pending)
                if nbr is None:                  # 邻域无效
                    continue
                E_new, E_cur = energy(nbr), energy(plan)

                if E_new < E_cur or random.random() < math.exp(-(E_new-E_cur)/T):
                    plan = nbr
                    if E_new < best_E:
                        best, best_E = nbr.clone(), E_new
            T *= self.alpha
        return best, still

    # ========= 生成邻域 (四种操作) ========= #
    def _neighbor(self, plan: ResourceModel, pending: List[Pod]) -> ResourceModel | None:
        if self.inc_mode:
            # 仅 Pending 期间产生的新实验 Pod
            exp_pods = [p.full_name
                        for nd in plan.nodes.values()
                        for p in nd.pods
                        if getattr(p, "is_new", False) and nd.name != "master"]
            allowed_ops = ("move", "swap", "open","upgrade_nwe","upgrade_nwe")  # close / upgrade 禁止
        else:
            # full : 所有实验 Pod
            exp_pods = [p.full_name
                        for nd in plan.nodes.values() if nd.name != "master"
                        for p in nd.pods]
            allowed_ops = ("move", "swap", "close", "open", "upgrade","upgrade")

        exp_pods = [p.full_name
                    for nd in plan.nodes.values() if nd.name != "master"
                    for p in nd.pods]
        allowed_ops = ("move", "swap", "close", "open", "upgrade", "upgrade")

        if not exp_pods and self.inc_mode:
            return None  # 增量但无可动 Pod
        op = random.choice(allowed_ops)
        new = plan.clone()

        if op == "move":
            if not exp_pods:
                return None
            p_full = random.choice(exp_pods)
            src_nd = new.nodes[new.pod2node[p_full]]
            pod = self._get_pod_obj(p_full, src_nd)
            if pod is None:
                return None
            tgt_nd = random.choice(list(new.nodes.values()))
            if tgt_nd == src_nd or tgt_nd.name == "master":
                return None
            pod = self._get_pod_obj(p_full, src_nd)
            if not tgt_nd.can_fit(pod):
                return None
            src_nd.rm_pod(pod); tgt_nd.add_pod(pod)
            new.pod2node[pod.full_name] = tgt_nd.name
            if self._constraints_ok(new):
                self._normalize(new)
                return new
            else:
                return None

        if op == "swap" and len(exp_pods) >= 2:
            p1, p2 = random.sample(exp_pods, 2)
            n1 = new.nodes[new.pod2node[p1]]
            n2 = new.nodes[new.pod2node[p2]]
            if n1 == n2 or "master" in (n1.name, n2.name):
                return None
            pd1 = self._get_pod_obj(p1, n1)
            pd2 = self._get_pod_obj(p2, n2)
            if pd1 is None or pd2 is None:
                return None  # 任何一边找不到 → 废弃本邻域
            if n1.can_fit(pd2) and n2.can_fit(pd1):
                n1.rm_pod(pd1); n2.rm_pod(pd2)
                n1.add_pod(pd2); n2.add_pod(pd1)
                new.pod2node[p1], new.pod2node[p2] = n2.name, n1.name
                if self._constraints_ok(new):
                    self._normalize(new)
                    return new
                else:
                    return None
            return None

        if op == "close":
            if self.inc_mode:
                return None
            idle = [nd for nd in new.nodes.values()
                    if nd.name != "master" and nd.name != "node-1" and nd.util_ratio <= 0.5]
            if not idle:
                return None
            nd = random.choice(idle)
            # 尝试把 Pod 迁走
            for p in list(nd.pods):
                placed = False
                for other in new.nodes.values():
                    if other == nd or other.name == "master":
                        continue
                    if other.can_fit(p):
                        nd.rm_pod(p); other.add_pod(p)
                        new.pod2node[p.full_name] = other.name
                        placed = True; break
                if not placed:
                    return None          # 关不掉

            if len(nd.pods) == 0:  # ← 修改判定：无实验 Pod
                # 强制把剩余资源标 0（system Pod 将随节点删除）
                nd.cpu_used = 0  # ← 修改
                nd.mem_used = 0  # ← 修改
                new.close_node(nd)
            if self._constraints_ok(new):
                self._normalize(new)
                return new
            else:
                return None

        if op == "open":
            # 若触顶，直接放弃
            if not self._can_add_node(new):
                return None
            if not pending:
                return None
            pd = random.choice(pending)
            nd = self._pick_machine(pd, new)
            if nd is None:
                return None
            nd.add_pod(pd)
            new.open_node(nd)
            new.pod2node[pd.full_name] = nd.name
            if self._constraints_ok(new):
                self._normalize(new)
                return new
            else:
                return None

        # ---------- UPGRADE：用大机型合并 1~2 个小节点 ----------
        if op == "upgrade":
            if self.inc_mode:
                return None
            # a) 选一个低利用率节点
            low = [nd for nd in new.nodes.values()
                   if nd.name != "master" and nd.name != "node-1" and nd.util_ratio <= 0.4]
            if not low:
                return None
            src1 = random.choice(low)

            # b) (可选) 再随机挑同一区另外一个低负载节点一起并
            tgt_group = [src1]
            other_low = [nd for nd in low if nd != src1]  # ← 修改：不再按 region 过滤
            if other_low and random.random() < 0.5:  # 50% 概率合并两台
                tgt_group.append(random.choice(other_low))

            need_cpu = sum(p.cpu for nd in tgt_group for p in nd.pods)
            need_mem = sum(p.mem for nd in tgt_group for p in nd.pods)

            # 现有 CPU 容量（worker）
            cpu_used = sum(nd.cpu_cap for nd in new.nodes.values() if nd.name != "master")
            cpu_allow = MAX_CLUSTER_CPU - cpu_used + sum(nd.cpu_cap for nd in tgt_group)

            # 找可行机型
            cand: list[tuple[int, float, float, str, str]] = []  # (cpuWaste,suit,price,region,mt)
            rho_pod = need_cpu / need_mem if need_mem else float("inf")

            for mt, (vcpu, mem) in self.seed.spec_map[src1.region].items():
                if vcpu - Node.DEFAULT_OVERHEAD_CPU  < need_cpu or mem < need_mem:
                    continue
                if vcpu > cpu_allow:
                    continue
                price = self.seed.price_map.get(src1.region, {}).get("OnDemand", {}).get(mt, 1e9)
                if price <= 0 or price == 1e9:
                    continue
                suit = abs(rho_pod - vcpu / mem) / (vcpu / mem + 1e-6)
                cand.append((vcpu - need_cpu, suit, price, src1.region, mt))

            if not cand:
                return None

            cand.sort(key=lambda t: (t[0], t[1], t[2]))
            waste, suit, price, region, mt = cand[0]

            # c) 创建新节点并迁移 Pod
            vcpu, mem = self.seed.spec_map[region][mt]
            new_nd = Node(f"up-{region}-{mt}-{random.randint(10000, 99999)}",
                          region, mt, vcpu, mem, price)
            new.open_node(new_nd)

            # 把组内 Pod 按 CPU 大到小依次迁入
            pods_to_move = []
            for nd in tgt_group:
                pods_to_move.extend(nd.pods)  # nd.pods 已保证全是实验 Pod
            for p in pods_to_move:
                if not new_nd.can_fit(p):
                    # 失败：回滚
                    return None
                old_nd = new.nodes[new.pod2node[p.full_name]]
                old_nd.rm_pod(p)
                new_nd.add_pod(p)
                new.pod2node[p.full_name] = new_nd.name

            # d) 关闭旧节点
            for nd in tgt_group:
                if nd.name not in new.nodes:
                    continue
                if len(nd.pods) == 0:  # ← 修改判定：无实验 Pod
                    # 强制把剩余资源标 0（system Pod 将随节点删除）
                    nd.cpu_used = 0  # ← 修改
                    nd.mem_used = 0  # ← 修改
                    new.close_node(nd)

            if self._constraints_ok(new):
                self._normalize(new)
                return new
            else:
                return None

        if op == "upgrade_new":
            # 仅增量模式触发；已 running 节点不参与
            new_nodes = [nd for nd in new.nodes.values()
                         if not nd.is_existing and nd.name != "master" and nd.name!="node-1"]
            if len(new_nodes) < 2:
                return None

            # a) 选一个 region，且该 region 至少有 2 台新节点
            from collections import defaultdict
            reg_map = defaultdict(list)
            for nd in new_nodes:
                reg_map[nd.region].append(nd)
            regions = [r for r, lst in reg_map.items() if len(lst) >= 2]
            if not regions:
                return None
            region = random.choice(regions)
            nd1, nd2 = random.sample(reg_map[region], 2)  # 待合并节点

            # b) 计算合并所需总资源
            need_cpu = nd1.cpu_cap + nd2.cpu_cap
            need_mem = nd1.mem_cap + nd2.mem_cap

            # c) 搜索在该 region 合规且更大机型
            cand = []
            for mt, (vcpu, mem) in self.seed.spec_map[region].items():
                if vcpu - Node.DEFAULT_OVERHEAD_CPU  < need_cpu or mem < need_mem:
                    continue
                price = self.seed.price_map.get(region, {}).get("OnDemand", {}).get(mt, 1e9)
                if price <= 0 or price == 1e9:
                    continue
                waste_cpu = vcpu - need_cpu
                cand.append((waste_cpu, price, mt, vcpu, mem))

            if not cand:
                return None
            cand.sort(key=lambda x: (x[0], x[1]))  # CPU 浪费最少 → 价格

            _, price, mt, vcpu, mem = cand[0]

            # d) 创建新节点
            merge_nd = Node(f"inc-up-{region}-{mt}-{random.randint(10000, 99999)}",
                            region, mt, vcpu, mem, price, is_existing=False)
            new.open_node(merge_nd)

            # e) 把 nd1、nd2 上全部 Pending Pod 迁到 merge_nd
            for src in (nd1, nd2):
                for p in list(src.pods):
                    if not merge_nd.can_fit(p):
                        return None  # 不应发生，安全检查
                    src.rm_pod(p)
                    merge_nd.add_pod(p)
                    new.pod2node[p.full_name] = merge_nd.name

            # f) 删除旧新节点条目（它们尚未真正创建，可直接移除）
            del new.nodes[nd1.name]
            del new.nodes[nd2.name]

            # 约束检查
            if self._constraints_ok(new):
                self._normalize(new)
                return new
            return None

        return None  # 其它情况

    # ========= 约束检测 ========= #
    def _constraints_ok(self, plan: ResourceModel) -> bool:
        workers = [n for n in plan.nodes.values() if n.name != "master"]
        if len(workers) > MAX_WORKER_NODES:
            return False
        cpu_total = sum(n.cpu_cap for n in workers)
        return cpu_total <= MAX_CLUSTER_CPU

    def _can_add_node(self, plan: ResourceModel) -> bool:
        workers = [n for n in plan.nodes.values() if n.name != "master"]
        if len(workers) >= MAX_WORKER_NODES:
            return False
        cpu_used = sum(n.cpu_cap for n in workers)
        return cpu_used < MAX_CLUSTER_CPU

    # ========= 机型选择（遵照 RFSA 逻辑，但带硬约束） ========= #
    def _pick_machine(self, pod: Pod, plan: ResourceModel) -> Node | None:
        need_cpu, need_mem = pod.cpu, pod.mem
        cpu_allow = MAX_CLUSTER_CPU - sum(
            nd.cpu_cap for nd in plan.nodes.values() if nd.name != "master"
        )
        if need_cpu > cpu_allow:
            return None

        rho_pod = need_cpu / need_mem if need_mem else float("inf")
        cand: list[tuple[int, float, float, str, str]] = []   # (cpuWaste,suit,price,region,mt)

        spec_map = self.seed.spec_map
        price_map = self.seed.price_map

        for region, mts in spec_map.items():
            for mt, (vcpu, mem) in mts.items():
                if vcpu - Node.DEFAULT_OVERHEAD_CPU  < need_cpu or mem < need_mem:
                    continue
                if vcpu > cpu_allow:
                    continue
                price = price_map.get(region, {}).get("OnDemand", {}).get(mt, 1e9)
                if price <= 0 or price == 1e9:
                    continue
                suit = abs(rho_pod - vcpu/mem) / (vcpu/mem + 1e-6)
                cand.append((vcpu-Node.DEFAULT_OVERHEAD_CPU -need_cpu, suit, price, region, mt))

        if not cand:
            return None

        cand.sort(key=lambda t: (t[0], t[1], t[2]))
        waste, suit, price, region, mt = cand[0]
        node_name = f"sa-{region}-{mt}-{random.randint(10000,99999)}"
        vcpu, mem = spec_map[region][mt]
        return Node(node_name, region, mt, vcpu, mem, price)

    # ========= 工具：通过 full_name 拿 Pod 对象 ========= #
    @staticmethod
    def _get_pod_obj(full_name: str, node: Node) -> Pod | None:
        """
        返回节点里 ‘真实’ 的 Pod 对象。
        若未找到，则返回 None（不再造 stub 对象）。
        """
        for p in node.pods:
            if p.full_name == full_name:
                return p
        return None

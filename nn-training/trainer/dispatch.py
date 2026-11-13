"""RolloutDispatcher —— 中央队列调度（2026-09-02 从 trainer/dispatch_queue.py 类化）。"""

from __future__ import annotations

import json
import random
import secrets
import threading
import time
from collections import deque
from pathlib import Path

import common.distribution
import common.protocol
from common.log import log

# 本地对局子进程的无窗口 spawn 由 `trainer.queue_local` 负责（Windows 下不弹黑色 cmd 抢焦点）。
from common.proc import bun_version as _bun_version
from common.proc import version_mm as _mm
from remote import weights_standard
from trainer.dispatch_state import DispatchState, admit_rollout_nodes
from trainer.queue_local import (
    QueueEdgeGate,
    make_local_pool,
    resolve_tail_join_sec,  # noqa: F401  (re-export: tests/trainer/test_tail_grace.py)
    run_local_rollout,
    run_rollout,
    split_local_head,
    waste_block,
)
from trainer.queue_local import (
    ensure_games as _ensure_games,
)
from trainer.queue_worker import (
    RACE_LOG_SAMPLE,
    QueuePlan,
    QueueState,
    run_queue,
)
from worker import serve_pool
from worker.agent_meta import record_agent_meta as _record_agent_meta_impl
from worker.reports import combine_reports
from worker.resume import completed_pairs, resumed_manifests, state_init_enabled
from worker.sibling_reuse import reuse_rollouts

REPO_ROOT = Path(__file__).resolve().parents[2]

# 分布式采样（plan/distributed-rollout.md v3.3）：runId 使 iterId={runId}.{it} 全局唯一，
# 杜绝 relaunch 后旧 run 未取结果与新 run 任务在 agent 侧混叠。
RUN_ID = secrets.token_hex(8)

# F6（plan/dist-codehash-stale-fix.md）：dedup 静默排除的显式告警计数。RolloutDispatcher
# 每迭代新建实例，计数器必须挂模块级才跨轮累积；连续 ≥3 轮 dedup 且非 self ⇒ WARN。
# 非 dedup 结局（节点 rejoin / restart-requested / dirty-tree / restart-failed）清零。
DEDUP_STREAK: dict[str, int] = {}




# 版本探测 / major.minor 比对 / 节点采样账本 —— 唯一实现见 `common.proc` 与
# `biz.agent_meta`（本处 re-export：本模块内的调用点与测试的 monkeypatch 接缝都不变）。
# 历史：本模块与 `trainer/dispatch_queue.py` 各有一份字形相同的三件套（bun_version / mm /
# _record_agent_meta），差别只在哪一份先被谁 import——两份注释都在解释同一件事。
bun_version = _bun_version
mm = _mm
_record_agent_meta = _record_agent_meta_impl


class RolloutDispatcher:
    """中央队列调度（原 run_rollout_queue 函数类化）。

    OO 封装：配置收为实例属性；`run()` 只拥有本机长驻池的生命周期，`_run` 按
    ① ping 门 → ② 权重下发 → ③ 队列/线程 → ④ 聚合拆成方法，相位产物走 `DispatchState`。
    """

    def __init__(
        self,
        bun: str,
        rl_path: str,
        traj_dir: Path,
        pairs: list[tuple[int, int]],
        args,
        cfg: dict,
        iter_id: str,
        on_result=None,
        local_slots_max: int | None = None,
        tail_dispatch: bool = True,
        halt_event: threading.Event | None = None,
        on_queue_drained=None,
        # 队列从「已全部派出」重新出现可派任务时回调（stream 用它重新冻结 eval）。
        on_queue_active=None,
        local_suspend: threading.Event | None = None,
        extra_wver: str | None = None,
        course_fp: str | None = None,
        corpus_fp: str | None = None,
        pool_halt: bool = False,
        # 零落盘（plan/payload-zero-disk §3.1）：`(shard 名, files, manifest)` 回调，
        # 经 `common.distribution.write_shard(on_receive=…)` 在**落盘之前**交出去 ——
        # dispatch 腿的字节本来就在内存里，调用方（`ShardSegments.add`）当场打段+压好。
        # 缺省 None = 逐字节旧行为（其它调用方无感）。
        on_shard=None,
    ):
        self.bun = bun
        self.rl_path = rl_path
        self.traj_dir = traj_dir
        self.pairs = pairs
        self.args = args
        self.cfg = cfg
        self.iter_id = iter_id
        self.on_result = on_result
        self.local_slots_max = local_slots_max
        self.tail_dispatch = tail_dispatch
        self.halt_event = halt_event
        self.on_queue_drained = on_queue_drained
        self.on_queue_active = on_queue_active
        self.local_suspend = local_suspend
        self.extra_wver = extra_wver
        self.course_fp = course_fp
        self.corpus_fp = corpus_fp
        # ★ `pool_halt`（2026-10-21 用户口径）：`halt` 是**候选池铺满后逐关满额**的停 ⇒
        #   池里没派出去的候选不算「缺口」（它们从不运行）。外部熔断（KL halt 等）不置位
        #   本标记 ⇒ `missing` 保持旧语义（没派的局照旧报出来，e2e 的 halt preset 钉着它）。
        self.pool_halt, self.on_shard = bool(pool_halt), on_shard
        #: 本机槽的长驻池（`_run` 里按需建；生命周期 = 这一轮，见 `run`）。
        self._pool: serve_pool.ServePool | None = None

    def run(self) -> dict:
        """轮次入口 —— **拥有本机长驻池的生命周期**（池在 `_run` 里按需创建）。

        单独一层的原因（2026-09-25）：`_run` 有多条提前 return（全部已续跑 / 已结算 /
        halt 熔断），池必须在**每一条**出口都 `close()` —— 否则每轮留下一批常驻 bun
        （本机槽有多宽就留几个，而它们会一直等 stdin）。
        """
        try:
            return self._run()
        finally:
            pool = self._pool
            if pool is not None:
                self._pool = None
                log(f"[dist] 本机 {pool.summary()}")
                pool.close()

    def _run(self) -> dict:
        """轮次编排：建相位状态 → ① ping 门 → ② 权重下发 → ③ 队列 + ④ 报告。"""
        # 相位状态替代旧的 15 个局部别名；每相位只把自己产出的字段写回去。
        st = DispatchState(
            bun=self.bun, rl_path=self.rl_path, traj_dir=self.traj_dir,
            pairs=self.pairs, args=self.args, cfg=self.cfg,
            iter_id=self.iter_id, on_result=self.on_result,
            local_slots_max=self.local_slots_max, halt_event=self.halt_event,
            local_suspend=self.local_suspend, extra_wver=self.extra_wver,
            course_fp=self.course_fp, corpus_fp=self.corpus_fp,
            t_queue_enter=time.time(),
        )
        early = self._ping_gate(st)
        if early is not None:
            return early
        self._prepare_weights(st)
        return self._run_queue(st)

    def _ping_gate(self, st: DispatchState) -> dict | None:
        """① ping 门：codeHash / bunVersion / bootId 全过才进本轮节点表。

        返回非 None = 本轮提前结束（state_init 纯本机 / 无可用节点）。
        """
        bun, rl_path, traj_dir = st.bun, st.rl_path, st.traj_dir
        pairs, args, cfg = st.pairs, st.args, st.cfg
        iter_id = st.iter_id
        policy = cfg.get("policy", {})
        task_timeout = float(policy.get("taskTimeoutSec", 900))
        window = float(policy.get("queueWindowSec", 1800))
        status_timeout = common.distribution.status_timeout_of(policy)
        fail_streak_max = int(policy.get("nodeFailStreak", 3))
        # 主动升级机制（M8）：ping 发现 codeHash **stale** 的节点 → POST /v1/restart
        # 指示它 git pull + 重启（本轮不参与，重启后 rescan 纳入）；拿不到升级分支时
        # 只告警、不重启（下面 `if not upgrade_branch`）。
        # 节点远控升级分支：**永远**以训练机当前分支为准（run_rl 启动时锁存到
        # common.distribution.UPGRADE_BRANCH）。**不读 config 的 upgradeBranch**：
        # 2026-08-30 事故里残留的 'intent-ai' 把全部节点 reset 回旧代码，而当时的
        # `upgrade_branch_or(explicit)` 是「显式值优先于锁存」⇒ 只要配置里有非空值就
        # 盖掉锁存。该键已从 rl-config.json 删除（plan/rl-config-cleanup.plan.md §3.1），
        # 读点去掉；2026-09-26 评审又把那个 `explicit` 参数一并拿掉
        # （`current_upgrade_branch()`）⇒ 坑在**签名层面**不再可能复现。
        upgrade_branch = common.distribution.current_upgrade_branch()
        wver = common.distribution.weights_fingerprint(rl_path)
        local_bun = bun_version(bun)
        iter_no = int(iter_id.rsplit(".", 1)[-1])
        meta_path = (
            traj_dir.parent / "dist-agent-meta.jsonl"
        )  # traj 根，跨轮累积（不被 keep-iters 清理）

        # ① ping 门：codeHash 一致 ∧ bunVersion major.minor 一致（确定性红线，M4）
        # v4.0 ping-first 并行化（用户指令 2026-08-29，移植 m1-eval 激活模式）：死节点的
        # ping 超时（statusTimeoutSec=3s）全部并行——7 死节点从串行 ~21s/轮 压到 ~3s；
        # 判定与日志按配置顺序串行回放（保序、保线程安全）。节点中途上线的接管仍靠
        # 下一轮迭代的 ping 门（与 rollout 既有语义一致）。
        code_hash = common.distribution.compute_code_hash()
        # 脏工作区护栏（2026-09-01 重启循环修复①）：期望 codeHash 由训练机工作区算出，
        # 含未提交改动时远端 git pull 永不收敛 ⇒ 对远端节点下发 pull+restart 是无效扰动
        # （实测：节点拉到最新提交后 hash 仍不等 → 每轮 rescan 再杀一次，无限重启循环）。
        # 每轮检测一次；self/回环节点不受限（代码同源，纯重启即可拾取工作区代码）。
        dirty_files = common.distribution.dirty_hash_files()
        if dirty_files:
            log(
                f"[dist] WARN: {len(dirty_files)} uncommitted file(s) in codeHash set "
                f"({', '.join(dirty_files[:5])}{', …' if len(dirty_files) > 5 else ''}) — "
                f"remote restart suppressed until committed+pushed"
            )
        # push 节点（`gpu_push` / `offline_push`）没有 `/v1/ping`：留在候选表里只会每轮换一条
        # `ping failed — excluded this round`（判据见 `common/protocol.sampler_nodes`）。
        cfg_nodes = common.protocol.sampler_nodes(cfg.get("nodes", []))

        def _probe(n: dict):
            nid = str(n.get("id") or n.get("url") or "?")
            return nid, common.distribution.node_ping(
                n["url"], n.get("authKey", ""), timeout=status_timeout
            )

        probe_results: list = []
        if cfg_nodes:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(8, len(cfg_nodes))) as _ex:
                probe_results = list(_ex.map(_probe, cfg_nodes))

        # 准入判据从 `st` 读：先把本相位算出的字段写进去（方法末尾的写入是给后续相位的）。
        st.code_hash, st.upgrade_branch = code_hash, upgrade_branch
        st.dirty_files, st.local_bun = dirty_files, local_bun
        st.meta_path, st.iter_no = meta_path, iter_no
        nodes = admit_rollout_nodes(st, probe_results, cfg_nodes, DEDUP_STREAK)
        # ── 起始分布（state_init）：整轮纯本机（2026-09-25 路由修正）───────────────────
        # v1 的快照**只在本机盘上**（云侧搬运 = plan/x20-state-init.plan.md §P2.5，未落地）：
        # 只有本机腿的 argv 走 `build_rollout_cmd(node_side=False)` 拿得到 `--init-snapshot`，
        # 而节点腿（`fetch_task` / `template_argv`）拼任务参数时**没有**快照这一项，且导出器
        # 对未知 flag **静默忽略** ⇒ 向 pool 派发 = 云上跑标准开局、账本记中段起跑，缺 `initTick`
        # 的 shard 还会被护栏在六个 funnel 全剔（采样波次永远凑不齐 = 无限波次；2026-09-25
        # 实测烧掉 51.9 万 transitions）。
        # 不看节点健康度："有节点就拒"等于构造性不可跑（self 也算节点，pool 健康时永远被拒，
        # 而"只留本机"的自救指南是错的——只留 self 照样 abort）；"放行 local 节点"也不对
        # （fetch 协议没有快照项，放行≠给快照）。改路由不改放行名单：开了 state_init 的轮
        # **整轮纯本机、向 pool 零派发**——混语料无从发生，波次自然收敛。
        # additive-only：课程没开 state_init ⇒ 本段一行不执行，逐字节旧行为。
        if state_init_enabled(args):
            log(
                "[dist] state_init: local-only round — "
                "zero pool dispatch; snapshot threaded via --init-snapshot"
            )
            return run_rollout(bun, rl_path, traj_dir, pairs, args)

        if not nodes:
            log("[dist] no eligible node — falling back to local-only rollout")
            return run_rollout(bun, rl_path, traj_dir, pairs, args)
        st.policy = policy
        st.task_timeout = task_timeout
        st.window = window
        st.status_timeout = status_timeout
        st.fail_streak_max = fail_streak_max
        st.upgrade_branch = upgrade_branch
        st.wver = wver
        st.local_bun = local_bun
        st.iter_no = iter_no
        st.meta_path = meta_path
        st.cfg_nodes = cfg_nodes
        st.nodes = nodes
        return None

    def _prepare_weights(self, st: DispatchState) -> None:
        """② 权重下发准备：整份化、读字节、按 wver 分 reuse/need。"""
        args, rl_path = st.args, st.rl_path
        nodes, wver = st.nodes, st.wver
        # ② 权重下发准备（边分发边开采，2026-09-19 用户口径）：
        #    rollout 耗时 = 权重就绪开始分发 → 样本齐可交 PPO。
        #    同 wver 进程缓存 reuse 跳过 POST；need 节点 POST 成功瞬间孵化采样线程。
        #    pure_collect_sec = last_settle − t_dist_start（含与采集重叠的分发墙钟）。
        if getattr(args, "goal_rollout", False):
            wkind = "goal"
        elif getattr(args, "intent_rollout", False):
            wkind = "intent"
        else:
            wkind = "rollout"
        # ★ 2026-10-15：推给节点的字节必须是**整份** nn-weights-json —— 离线腿逐轮回传的是
        #   delta 容器（`BWD1`），hub 原样落位 ⇒ 指针可能正指向容器；节点侧 `JSON.parse` 会报
        #   `Unexpected identifier "BWD1"`。读侧落地（幂等；整份只读 4 字节）。
        weights_standard.ensure_standard_weights(rl_path, log=log)
        with open(rl_path, "rb") as f:
            weights_bytes = f.read()
        reuse, need = common.distribution.partition_weights_nodes(nodes, wver, kind=wkind)
        if reuse:
            log(
                f"[dist] weights[{wkind}] reuse wver={wver[:12]}… skip POST for "
                f"{[nd['id'] for nd in reuse]}"
            )
        # ping 失败/codeHash/bun 不符已在门内 forget；本列表在 worker 定义后用于
        # 边分发边开采的即时 spawn。
        # 最后一局成功结算的时刻（worker 内更新）。
        last_settle_at: list[float | None] = [None]
        # 权重分发起止（诊断 + pure_collect 新口径）。
        t_dist_start_box: list[float | None] = [None]
        t_dist_done_box: list[float | None] = [None]
        st.wkind = wkind
        st.weights_bytes = weights_bytes
        st.reuse = reuse
        st.need = need
        st.last_settle_at = last_settle_at
        st.t_dist_start_box = t_dist_start_box
        st.t_dist_done_box = t_dist_done_box

    def _queue_plan(self, st: DispatchState) -> QueuePlan | dict:
        """③a 计划：续跑过滤、同族复用；全在盘上时直接返回聚合报告。"""
        traj_dir, wver, extra_wver = st.traj_dir, st.wver, st.extra_wver
        course_fp, corpus_fp, args = st.course_fp, st.corpus_fp, st.args
        pairs, iter_id, local_slots_max = st.pairs, st.iter_id, st.local_slots_max
        # 断点续跑：剔除已完整落盘且 wver 匹配的局（本轮重启/重试不重跑已完成任务）。
        # pairs 元素强制 int 化：build_pairs 产生的元组可能是 numpy 标量，其 hash/相等 与
        # completed_pairs 返回的 Python int 元组不一致 → `in done` 过滤失效 → 重跑已完成局（浪费）。
        # norm_pairs 统一为 (int,int)，与 done 集合可比。
        norm_pairs = [(int(a), int(b)) for a, b in pairs]
        # 断点续跑按「本轮计划」口径：目录里可能有跨配置残留的同权重 shard（sps 变更后
        # 重启同一迭代，it60 实测目录 235 局/计划 105 局），它们不在新计划里——既不重跑
        # 也不并入报告。done 一词自此恒指计划内已完成。
        plan_set = set(norm_pairs)
        done_all = completed_pairs(
            traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
            state_init=state_init_enabled(args),
        )
        done = done_all & plan_set
        # 同族课程的现成局（plan/episode-reuse.plan.md §3 S3b）：命中即出队，不派节点、不重算，
        # 聚合由下面两个 `resumed_manifests` 从盘上收（与「重启撞上已落盘局」同一条路）——
        # 与 `local_slots` 无关，集群轮（本机槽 0）也不会把命中局再派给远端。
        tasks = reuse_rollouts([p for p in norm_pairs if p not in done], traj_dir, wver, args)
        if done_all:
            log(
                f"[dist] resume: {len(done)}/{len(norm_pairs)} planned pairs already on disk — "
                f"run {len(tasks)} remaining"
                + (
                    f" (ignoring {len(done_all) - len(done)} off-plan shards)"
                    if len(done_all) != len(done)
                    else ""
                )
            )
        if not tasks:
            log(
                "[dist] all planned pairs already on disk — aggregating report from shards "
                "(PPO will resume/replay)"
            )
            # 从磁盘 shard 聚合而非返回空报告：空报告曾让 it1 的 winRate/samples
            # 全为零（指标盲区）；only=plan_set 保证跨配置残留下聚合口径仍等于本轮计划。
            # 补齐 missing/expectedGames/dist：与全流程路径同 schema，下游免分支。
            combined = combine_reports(
                resumed_manifests(
                    traj_dir,
                    wver,
                    only=plan_set,
                    extra_wver=extra_wver,
                    course_fp=course_fp,
                    corpus_fp=corpus_fp,
                    state_init=state_init_enabled(args),
                )
            )
            combined["missing"] = []
            combined["expectedGames"] = len(pairs)
            combined["dist"] = {
                "iterId": iter_id,
                "nodes": {},
                "retried": 0,
                "resumed": len(done),
                "offPlanShards": max(0, len(done_all) - len(done)),
            }
            return combined
        random.Random(f"queue:{RUN_ID}:{iter_id}").shuffle(tasks)  # 队列可复现；分配依实时负载
        if local_slots_max is not None:
            local_slots = max(0, min(int(local_slots_max), len(tasks)))
        else:
            local_slots = max(1, min(args.workers, len(tasks)))
        return QueuePlan(
            norm_pairs=norm_pairs,
            plan_set=plan_set,
            done=done,
            done_all=done_all,
            tasks=tasks,
            local_slots=local_slots,
        )

    def _queue_setup(self, st: DispatchState, plan: QueuePlan) -> QueueState:
        """③a 状态：本机保留段、长驻池、共享容器与调度旋钮。"""
        bun, rl_path, traj_dir = st.bun, st.rl_path, st.traj_dir
        args, cfg, iter_id = st.args, st.cfg, st.iter_id
        on_result, local_suspend = st.on_result, st.local_suspend
        extra_wver, course_fp, corpus_fp = st.extra_wver, st.course_fp, st.corpus_fp
        policy, task_timeout, window = st.policy, st.task_timeout, st.window
        status_timeout, fail_streak_max = st.status_timeout, st.fail_streak_max
        upgrade_branch, wver, local_bun = st.upgrade_branch, st.wver, st.local_bun
        iter_no, meta_path = st.iter_no, st.meta_path
        code_hash, dirty_files = st.code_hash, st.dirty_files
        wkind, weights_bytes = st.wkind, st.weights_bytes
        reuse, need = st.reuse, st.need
        last_settle_at, t_dist_start_box = st.last_settle_at, st.t_dist_start_box
        t_dist_done_box, halt_event = st.t_dist_done_box, st.halt_event

        norm_pairs, plan_set = plan.norm_pairs, plan.plan_set
        done, done_all = plan.done, plan.done_all
        tasks, local_slots = plan.tasks, plan.local_slots
        n_total_tasks = len(tasks)
        # R6：--local-slots 头部分配——洗牌后的前 local_slots 个任务划入本机专用队列，
        # 节点线程不可触及（确定性「dist 阶段最先被分派」）；本机让位（local_suspend）
        # 时一次性并回主队列交远端消化，防挂死。
        # B3：本机保留段按本机/远端轮转发牌，不再整段占队首（份额仍 = local_slots）。
        head, tasks = split_local_head(tasks, local_slots)
        head_tasks: deque[tuple[int, int]] = deque(head)
        # 本机槽的长驻池（`--serve`，与节点侧 `worker/serve_pool.py` 同一份协议）：
        # 逐局 spawn 时每局重付进程启动 / wasm 编译 / 权重解析，池化后只付一次（节点侧实测
        # 1.59×，docs/nn/runtime-opt.md §20）。宽度 = 本机槽位数；池起不来或某局跑挂就地回退
        # 一次性 spawn（`make_local_pool` / `serve_pool` 内部带熔断 —— 只慢不错）。
        probe = head_tasks[0] if head_tasks else (tasks[0] if tasks else None)
        local_pool = make_local_pool(bun, rl_path, traj_dir, probe, args, wver, local_slots)
        self._pool = local_pool
        all_tasks = list(tasks)  # 全量任务清单（含已划入本机保留段的）——完成判定/missing 口径
        pending: deque[tuple[int, int]] = deque(tasks)
        lock = threading.Lock()
        seen: set[tuple[int, int]] = set()
        attempts: dict[tuple[int, int], int] = {}
        streaks = {nd["id"]: 0 for nd in st.nodes}
        # 瞬时（背压/瞬断）连续计数：与真故障分开。瞬时错误**不计**节点击败
        # streak（502 隧道/10054/超时/503 busy 都是可恢复的），但连续软失败仍有
        # 上界——否则「整个集群或隧道挂了」时会无限重排把整轮拖到窗口超时。
        # 一局成功即清零。`policy.nodeSoftFailStreak` 可覆盖（缺省 3×）。
        soft_streak_max = int(policy.get("nodeSoftFailStreak", fail_streak_max * 3))
        soft_streaks: dict[str, int] = {}
        results: list[dict] = []
        stats = {"retried": 0, "alloc_timeout_requeued": 0, "late_original_settled": 0}
        # 竞速输家/dup settle 是 fan-out 的正常结局，逐条打会刷爆 training-loop.log
        # （实测单轮数百行）。每类只留前 RACE_LOG_SAMPLE 条作证据，其余进 round-done 汇总。
        race_drops = {
            "dup_settle": 0,
            "fanout_settled": 0,
            "fanout_inflight": 0,
            # 2026-10-21 评审 P1：节点按 busy 直接拒收的副本——**一局没跑**，不算重复执行。
            # 与 `fanout_inflight`（副本在飞时主副本先返回，副本跑没跑完未知）分开记，
            # 否则 `dup_rate` 的分子里混着一堆零成本拒收（现场 503 常见）⇒ 候选①被推成假大头。
            "fanout_rejected": 0,
            "main_by_fanout": 0,
        }
        missing_keys: set[tuple[int, int]] = set()
        all_settled = threading.Event()  # 成功+永久缺失 == 总局数 时置位，worker 立即收工
        deadline = time.time() + window
        next_idx = [0]
        edges = QueueEdgeGate(self.on_queue_drained, self.on_queue_active, log)
        # 远端集体失联保护：本机先只孵化 local_slots 条线程（0 = 不孵化）；
        # 连续 remoteDeadSecs 无任何远端结算时，由本机守护线程补孵到 cap_full，
        # 保证 PPO 语料供应不被死掉的远端拖垮。
        cap_full = max(1, min(args.workers, n_total_tasks))
        local_cap = [local_slots]
        local_active = [0]
        # 有效并行度的分子：**节点服务时长**之和（`elapsedSec`，不含排队/取包）。
        # 用训练机侧 wall 会把网络等待算成「占着槽」，E 会虚高（plan/rollout-corpus-supply §3.4）。
        # ★ 逐节点分桶（2026-10-21 评审 P1）：只报一个总和时，「远端只出 29% 的活」既可能是
        #   「远端每任务慢」也可能是「队列先到先得、localhost 零延迟抢走队首」——两者的刀不同。
        #   分桶后 `利用率 = sumGameSecByNode[n] / (concurrency[n] × 采集墙钟)` 可直接判。
        sum_game_sec = [0.0]
        sum_game_sec_by_node: dict[str, float] = {}
        last_remote_ok = [time.time()]
        # 2026-11-10（B3）：初始线程数就是 local_slots，不再预孵化 cap_full 条空转
        # 线程；远端失联后由 watchdog 补孵。thread_slots 是初始值，fallback 是上限。
        log(
            f"[dist] slots: local_slots={local_slots} (max={st.local_slots_max}) "
            f"cap_full={cap_full} workers={args.workers} "
            f"thread_slots={local_slots} fallback={cap_full} "
            f"nodes={[(n['id'], n['c']) for n in st.nodes]}"
        )
        remote_dead_sec = float(policy.get("remoteDeadSecs", 150))
        # 2026-09-16 用户裁定：去掉 EWMA 快慢 hold / tail fan-out / dup 上限。
        # 分派不判节点快慢；pending 有活谁空谁接；pending 清空后空槽对**其它节点
        # 正在跑的局**做 in-flight race（同节点不派回；先返回者结算、败者丢弃）。
        # 副本数天然上界 = 节点数。it24 实锤：hold 使 self/mac 空转 16s 等 a95。
        # v3.15 分配超时重入（taskFetchTimeoutSec）与冷却黑名单保留。
        task_fetch_timeout = float(policy.get("taskFetchTimeoutSec", 30))
        # v3.9 动态节点发现 + A4 轮内回场（2026-09-19 审计）：跑批中途上线的 agent、
        # 以及**被熔断/软停后恢复的节点**都能回场（0 = 关闭）。
        #  cadence：`recoverPingSec`（缺省 20s，与 B/C 层同口径）> `agentRescanSec`（旧名，
        #  显式设 0 即关闭）> 20s。首个 pass 由 `nodeRecoverFirstSec`（缺省 5s）决定——
        #  旧实现首个 pass 要等满 120s，而 volume 短波 234 轮全部 <120s ⇒ 一次都没跑过。
        rescan_sec = float(policy.get("recoverPingSec", policy.get("agentRescanSec", 20)))
        recover_first_sec = float(policy.get("nodeRecoverFirstSec", 5.0))
        rearm_cap = int(policy.get("nodeRearmLimit", 3))
        transient_backoff_sec = float(policy.get("transientBackoffSec", 5.0))

        inflight: dict[tuple[int, int], int] = {}
        inflight_ts: dict[tuple[int, int], float] = {}
        inflight_nodes: dict[tuple[int, int], set[str]] = {}
        task_timeout_blocks: dict[tuple[int, int], set[str]] = {}
        inflight_tags: dict[tuple[int, int], set[str]] = {}
        inflight_targets: dict[tuple[int, int], dict[str, dict]] = {}
        tag_seq = [time.time_ns()]
        return QueueState(
            bun=bun, rl_path=rl_path, traj_dir=traj_dir, args=args, cfg=cfg,
            iter_id=iter_id, on_result=on_result, local_suspend=local_suspend,
            extra_wver=extra_wver, course_fp=course_fp, corpus_fp=corpus_fp,
            policy=policy, task_timeout=task_timeout, status_timeout=status_timeout,
            fail_streak_max=fail_streak_max, upgrade_branch=upgrade_branch, wver=wver,
            local_bun=local_bun, iter_no=iter_no, meta_path=meta_path,
            code_hash=code_hash, dirty_files=dirty_files, wkind=wkind,
            weights_bytes=weights_bytes, reuse=reuse, need=need,
            n_total_tasks=n_total_tasks, norm_pairs=norm_pairs,
            remote_dead_sec=remote_dead_sec,
            task_fetch_timeout=task_fetch_timeout,
            transient_backoff_sec=transient_backoff_sec,
            soft_streak_max=soft_streak_max, local_slots=local_slots,
            cap_full=cap_full, rescan_sec=rescan_sec,
            recover_first_sec=recover_first_sec, rearm_cap=rearm_cap,
            on_shard=self.on_shard, log=log, record_agent_meta=_record_agent_meta,
            run_local_rollout=run_local_rollout,
            pending=pending, head_tasks=head_tasks, lock=lock, seen=seen,
            attempts=attempts, streaks=streaks, soft_streaks=soft_streaks,
            results=results, stats=stats, race_drops=race_drops,
            missing_keys=missing_keys, all_settled=all_settled, deadline=deadline,
            next_idx=next_idx, edges=edges, local_cap=local_cap,
            local_active=local_active, sum_game_sec=sum_game_sec,
            sum_game_sec_by_node=sum_game_sec_by_node, last_remote_ok=last_remote_ok,
            inflight=inflight, inflight_ts=inflight_ts,
            inflight_nodes=inflight_nodes,
            task_timeout_blocks=task_timeout_blocks, inflight_tags=inflight_tags,
            inflight_targets=inflight_targets, tag_seq=tag_seq,
            last_settle_at=last_settle_at, t_dist_start_box=t_dist_start_box,
            t_dist_done_box=t_dist_done_box, all_tasks=all_tasks,
            plan_set=plan_set, done=done, done_all=done_all,
            local_pool=local_pool, halt_event=halt_event,
        )

    def _run_queue(self, st: DispatchState) -> dict:
        """③ 队列 + 消费 + 收尾 join：计划 → 状态 → 线程 → 写回。"""
        plan = self._queue_plan(st)
        if isinstance(plan, dict):
            return plan
        q = self._queue_setup(st, plan)
        run_queue(q)
        st.plan_set = q.plan_set
        st.done = q.done
        st.done_all = q.done_all
        st.all_tasks = q.all_tasks
        st.n_total_tasks = q.n_total_tasks
        st.seen = q.seen
        st.attempts = q.attempts
        st.results = q.results
        st.stats = q.stats
        st.race_drops = q.race_drops
        st.halted = q.halted
        st.local_lane = q.local_lane
        st.sum_game_sec = q.sum_game_sec
        st.sum_game_sec_by_node = q.sum_game_sec_by_node
        return self._settle_round(st)

    def _settle_round(self, st: DispatchState) -> dict:
        """④ 收尾：缺口/竞速账/报告聚合与耗时字段。"""
        traj_dir, pairs, args = st.traj_dir, st.pairs, st.args
        iter_id, halt_event, extra_wver = st.iter_id, st.halt_event, st.extra_wver
        course_fp, corpus_fp, wver = st.course_fp, st.corpus_fp, st.wver
        nodes, plan_set, done = st.nodes, st.plan_set, st.done
        done_all, all_tasks, n_total_tasks = st.done_all, st.all_tasks, st.n_total_tasks
        attempts, seen, results = st.attempts, st.seen, st.results
        stats, race_drops, halted = st.stats, st.race_drops, st.halted
        local_lane, sum_game_sec = st.local_lane, st.sum_game_sec
        sum_game_sec_by_node = st.sum_game_sec_by_node
        t_dist_start_box, t_dist_done_box = st.t_dist_start_box, st.t_dist_done_box
        last_settle_at, t_queue_enter = st.last_settle_at, st.t_queue_enter
        # ★ 候选池的「满额即停」下，「缺口」只算真派出去过的（`attempts` 的 key）：池里没派的
        #   候选不是失败（2026-10-21）——否则 `missing` 会把整个未用的池报成失败。
        #   外部熔断（`pool_halt=False`）保持旧语义：没派的局照旧报，操作员要知道哪几局没跑。
        #   竞速副本不进 `attempts`：它们跑了却没回结果，halted 时算 missing 是正确语义。
        missing = sorted(k for k in (set(attempts) if halted and self.pool_halt else all_tasks) if k not in seen)
        by_node = common.distribution.by_node_of(results)
        log(
            f"[dist] round done: ok={len(results)}/{n_total_tasks} missing={len(missing)} "
            f"retried={stats['retried']} byNode={json.dumps(by_node)}"
        )
        # 竞速账 + 重复执行率（口径见 `common.distribution.race_stats`；2026-10-21 收口）
        if _race_line := common.distribution.race_log_line(
            race_drops, len(results), sample=RACE_LOG_SAMPLE
        ):
            log(_race_line)
        if missing:
            log(f"[dist] missing pairs: {[list(k) for k in missing]}")

        combined = combine_reports(
            [_ensure_games(r) for r in results]
            + resumed_manifests(
                traj_dir,
                wver,
                exclude=seen,
                only=plan_set,
                extra_wver=extra_wver,
                course_fp=course_fp,
                corpus_fp=corpus_fp,
                state_init=state_init_enabled(args),
            )
        )
        combined["missing"] = [list(k) for k in missing]
        combined["expectedGames"] = len(pairs)
        combined["dist"] = common.distribution.dist_block(
            iter_id=iter_id,
            by_node=by_node,
            nodes=nodes,
            retried=stats["retried"],
            resumed=len(done),
            off_plan=max(0, len(done_all) - len(done)),
            race_drops=race_drops,
            games=len(results),
            sum_game_sec=sum_game_sec[0],
            sum_game_sec_by_node=sum_game_sec_by_node,
        )
        combined["dist"]["waste"] = waste_block(stats, local_lane.idle_sec)
        combined["dist_phase_sec"] = round((t_dist_done_box[0] or time.time()) - t_queue_enter, 1)
        if halt_event is not None and halt_event.is_set():
            combined["halt_aborted"] = True
            log(
                f"[dist] KL halt active — dispatch stopped early "
                f"({len(missing)} task(s) left undispatched/unsettled)"
            )
        # rollout 采集耗时（用户口径 2026-09-19）：权重就绪开始分发 → 样本齐可交 PPO。
        # 边分发边开采下含与采集重叠的分发墙钟（端到端，不是「纯仿真」）。
        collect_sec = common.distribution.rollout_collect_sec(t_dist_start_box[0], last_settle_at[0])
        if collect_sec is not None:
            combined["pure_collect_sec"] = collect_sec
        # 有效并行度 E = Σ局服务时长 / 采集墙钟（Little's law，plan/rollout-corpus-supply §3.4）。
        # 判据：E 远低于在线槽数 ⇒ 缺口在「重复执行/远端慢」；E ≈ 在线槽 ⇒ 只能加槽。
        eff_slots = common.distribution.effective_slots(sum_game_sec[0], collect_sec)
        combined["dist"]["effSlots"] = eff_slots
        if eff_slots is not None:
            log(
                f"[dist] round eff: E={eff_slots} 槽（Σ局时长 {sum_game_sec[0]:.1f}s / 采集 {collect_sec}s）"
                f"，在线槽 {combined['dist']['slotsOnline']}，dup={combined['dist']['dupExecuted']}"
                f"+{combined['dist']['dupRejected']}拒收"
            )
        # 数值锚点：多波 volume 由 combine_reports 做 it 级 min→max 聚合。
        if t_dist_start_box[0] is not None:
            combined["weights_dist_start_ts"] = float(t_dist_start_box[0])
            combined["weights_dist_start_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(t_dist_start_box[0])
            )
        end_ts = last_settle_at[0]
        if end_ts is None:
            end_ts = t_dist_done_box[0] or time.time()
        combined["collect_end_ts"] = float(end_ts)
        if t_dist_done_box[0] is not None:
            combined["weights_dist_done_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(t_dist_done_box[0])
            )
        return combined

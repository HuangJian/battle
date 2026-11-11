"""干净评估分发：固定语料贪心局（旁路，绝不拖垮训练主循环）。

2026-11-04 任务级统一派发（plan/cloud-pool-cross-round.plan.md）：

* 轮只**登记**任务表与上下文（`worker/eval_arbiter.EvalRound`），不再各起一组线程；
* 线程住进程级池（`worker/eval_exec.py`），按节点生长，从 `EvalArbiter.take()` 领**下一局**：
  唯一任务按轮轮转（多轮平等）→ 跨轮竞速副本（空出来的槽位不闲置）→ 等待；
* rollout 只**冻结新投递**（`worker/eval_gate.py` 的 rollout 作用域 → 仲裁器 freeze）：
  在飞 eval 局自然落账，rollout 收尾后同一批线程继续服务；
* 同一时刻可以有多个轮 / 多门课的局在多个节点上并行——分派端只决定「下一局是谁」。

本模块只负责一轮的登记、节点门 + 权重下发、收口与三本账；执行面在
`worker/eval_exec.py`，轮中自救在 `worker/eval_self_rescue.py`，让位/份额判据与公式在
`worker/eval_yield.py`，行构造在 `worker/eval_local.py`。

时机由调用方决定（流式=派发队列清空时经 on_queue_drained → dispatch_eval_bg；
串行=rollout 返回后藏进 PPO 空窗）。任何失败只记日志，绝不抛出。
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import common.distribution
import common.protocol
from common.fs import append_jsonl
from common.log import log
from remote import weights_standard
from trainer.dispatch_queue import _record_agent_meta, bun_version, mm
from worker import node_identity
from worker.agent_meta import record_eval_failed
from worker.archive import find_archive_weights  # noqa: F401 — 兼容旧引用
from worker.eval_arbiter import EvalRound, eval_arbiter
from worker.eval_exec import eval_executors

# 同 queue.py：Windows 下隐藏本地评估子进程的控制台窗口（避免反复弹黑窗抢焦点）。
from worker.eval_local import (
    BASELINE_EVAL_ITER,
    EVAL_ITER_SUFFIX,
    EVAL_RECORD_RETRY_MAX,
    EVAL_TASK_ATTEMPTS,
    EVAL_TASK_ATTEMPTS_TRANSIENT,
    a_eval_seed_list,
    eval_done_keys,
    eval_row,
    report_winrate_safe,  # noqa: F401 — re-exported（旧模块成员，兼容外部引用）
    run_local_eval_game,
    settle_eval_summary,
)
from worker.eval_self_rescue import supervise_round
from worker.eval_track import course_logger, lost_by_node, reconcile_late_eval_rows

# 让位/份额（尾巴）策略在 `worker/eval_yield.py`：本派发器只消费判据——尾段预留量 /
# 宽限强制释放点 / 在飞落账宽限的**公式**也住那边（不再是内联表达式）。
from worker.eval_yield import (
    EVAL_LOCAL_SLOTS_DEFAULT,
    eval_close_reason,
    eval_request_scope,
    eval_slot_cap,
    inflight_grace_cap,
    release_local_gate_if_starved,
    reserve_local_slots,
    select_delayed_eval_it,  # noqa: F401 — re-exported（旧模块成员，兼容外部引用）
)
from worker.sibling_reuse import REUSE_NODE, reuse_eval_round

#: 干净评估的权重 kind（B6，2026-09-19）：节点按 (kind, wver) 分桶缓存权重
#: （sampler-agent `weightsByKindSha`）。旧实现与训练 rollout 共用 'rollout' 桶 ⇒ 训练每轮
#: 刷权重与 eval 那份在同桶内互相驱逐/清场，eval 局随即 409「wver not cached here」
#: （2026-09-19 审计 A1/B6；客户端只能靠 409 自愈重发兜底）。独立 kind 后两条腿互不驱逐，
#: 且日志与落盘文件（`weights-eval-<sha16>.json`）一眼可分。
#:
#: 协议侧 kind 是不透明字符串（POST x-kind / GET X-Kind / task ?kind= 三处同源）⇒
#: 旧节点无需任何改动，本改动不触 codeHash（nn-training/** 不在 SSOT 内）。
EVAL_WEIGHTS_KIND = "eval"


class EvalDispatcher:
    """固定语料干净评估的单轮派发器（阻塞版，调用方放后台线程跑）。

    节点门：enabled ∧ ping ∧ ping.evalSupport ∧ bun major.minor 一致——旧 agent 无
    能力声明即跳过（它会静默忽略 mode 参数把评估局跑成采样局），逐节点灰度点亮。

    本地参与有两种模式：

    * **份额模式**（`local_fallback_only=False`，缺省）：R6 语义——local_gate 由调用方在
      「训练侧已无梯度步可做」（PPO/采集收尾）时 set；本机槽位在节点门前先开工并预留
      队尾。手动 evalA / B/C / 旧调用方走这里。
    * **兜底模式**（`local_fallback_only=True`，in-loop）：只有本轮 **没有任何可用节点**
      （`nodes_ok` 为空：无 enabled 节点 / 全部 ping 或能力门失败 / 权重 POST 全败）才启动
      本机线程；健康集群上本机 0 线程、不预留队尾。local_gate 不参与。

    派发本身不再是「一轮一组线程」：任务进 `EvalRound` 后由全局线程池按仲裁顺序领走，
    所以两轮 eval 可以同时在这批节点上跑（`worker/eval_arbiter.py`）。
    """

    def __init__(
        self,
        bun: str,
        rl_path: str,
        traj_dir: Path,
        args,
        cfg: dict,
        iter_id: str,
        it: int,
        rollout_winrate: float | None = None,
        local_gate: threading.Event | None = None,
        baseline: bool = False,
        local_fallback_only: bool = False,
    ) -> None:
        self.bun = bun
        self.rl_path = rl_path
        self.traj_dir = traj_dir
        self.args = args
        self.cfg = cfg
        self.iter_id = iter_id
        self.it = it
        self.rollout_winrate = rollout_winrate
        self.local_gate = local_gate
        #: it0 基线模式：`rl_path` 是课程 bc 权重、`it` 是 0；已评估账本按 iter 去重
        #: （见 eval_done_keys 的 iter_filter），不与 A-eval 共用 wver 键发生互吞。
        self.baseline = baseline
        #: True = 本机只做「集群全部不可用」的兜底（in-loop 四个派发点）。
        self.local_fallback_only = local_fallback_only

    def run(self) -> bool:
        """登记一轮、下发权重、等收口并落 summary。

        返回 True 的旧语义是「被 rollout 插队收手」——任务级仲裁后**整轮不再被插队**
        （在飞局自然落账、只是新局冻结），所以恒返回 False；保留返回值为兼容调用方。
        """
        bun = self.bun
        rl_path = self.rl_path
        traj_dir = self.traj_dir
        args = self.args
        cfg = self.cfg
        iter_id = self.iter_id
        it = self.it
        rollout_winrate = self.rollout_winrate
        local_gate = self.local_gate
        # D5（plan/eval-gap-honesty §6-1）：本轮所有 eval 行自带课程标签、不经 `_PREFIX`。
        # 别叫 `log`：同名赋值会让模块全局变局部；`_log` 仍闭包到模块全局（tests patch `ed.log`）。
        _log = course_logger(str(getattr(args, "course", "") or ""), log)

        # D1b（§2b）：eval 的私有 scratch **不是** it<N>/——轮转删 it<N>/ 时不会带走在飞的评估。
        eval_dir = traj_dir.parent / f"_eval-{traj_dir.name}"
        arb = eval_arbiter()
        execs = eval_executors()
        rnd: EvalRound | None = None
        try:
            policy = cfg.get("policy", {})
            status_timeout = float(policy.get("statusTimeoutSec", 3))
            task_timeout = float(policy.get("taskTimeoutSec", 900))
            fail_streak_max = int(policy.get("nodeFailStreak", 3))
            window = float(getattr(args, "eval_window_sec", 1500) or 1500)
            # POST 预算只作**未就绪时**的临时窗口上限（窗口在权重就绪后才定案，2026-11-03）。
            post_budget = min(300.0, max(60.0, task_timeout))
            wver = common.distribution.weights_fingerprint(rl_path)
            key16 = wver[:16]
            # D14 课程血缘（节点 resultCache 键的课程隔离分量，2026-10-08）：
            # 多课程同进程共享 RUN_ID ⇒ eval iterId 恒同（`{RUN_ID}.{it}ev`）、stage/seed/
            # sjHash 也同 ⇒ 不带 course_fp 时兄弟课程的同 (stage,seed) 任务在任一节点
            # 同键互串——先落的那门课的结果带着自己的 wver 回给另一门课，被
            # validate_eval_result 拒绝，attempt 打光即 dropped（现场：x23-cm2/cm3 it50
            # 同秒派发、node mac 实缺 1 局；2026-10-06 x21-psh-b0 it155 同模式一次）。
            # rollout 腿早已带（D14），本腿补齐。延迟导入避免 biz.cmd ↔ 本模块环（同
            # summary 落账处的既有口径）。
            from worker.cmd import course_fp_for_args

            course_fp = course_fp_for_args(args)
            eval_iter_id = f"{iter_id}{EVAL_ITER_SUFFIX}"
            eval_jsonl = traj_dir.parent / "eval_log.jsonl"
            # 采样机健康账本：eval 局与 rollout 同册入账（mode:"eval" 标记区分）——
            # 巡检「采样机健康」表按本文件聚合，eval 不入账则节点贡献被系统性低估。
            meta_path = traj_dir.parent / "dist-agent-meta.jsonl"
            n_seeds = max(0, int(getattr(args, "eval_games_per_stage", 0) or 0))
            # 干净评估语料（goal-nn）：--eval-stages 非空 = 按规格解析（如 arena
            # '1000-1002'，训练场自评）；空 = 真实关 0..total_stages-1（旧行为）。
            eval_stage_spec = str(getattr(args, "eval_stages", "") or "")
            if eval_stage_spec:
                from biz.course import parse_range

                eval_stages = parse_range(eval_stage_spec)
            else:
                eval_stages = list(range(args.total_stages))
            # 固定池外语料（2026-10-22，DECISIONS §2026-10-22-fixed-offpool-eval-seeds）：
            # `a_eval_seed_list` 与 it 无关 ⇒ 每轮、每门同 level 的课都评同一批
            # (stage,seed)，it 之间/课程之间逐点可比。小 n_seeds（冒烟/单测）照旧切片。
            seed_list = a_eval_seed_list(it, n_seeds, baseline=self.baseline)
            pairs = [(s, sd) for s in eval_stages for sd in seed_list]
            if not pairs:
                return False
            # it0 基线：账本按 iter 隔离（baseline 行的 iter 恒 0），否则同指纹的
            # A-eval 行会把基线局算成"已评估"——it0 行永远不落盘、控制台退回旧基准。
            # A-eval 反向同理：min_iter=1 把 it0 基线行挡在 A-eval 去重外（bc 与
            # it1 的 args.out 指纹偶同时，it1 的 A-eval 不得被 it0 行整轮跳过）。
            done = (
                eval_done_keys(eval_jsonl, key16, BASELINE_EVAL_ITER)
                if self.baseline
                else eval_done_keys(eval_jsonl, key16, min_iter=1)
            )
            todo = [p for p in pairs if p not in done]
            if not todo:
                _log(
                    f"[eval] it{it}: wver={key16[:12]}… already evaluated — skip"
                    + ("（it0 基线）" if self.baseline else "")
                )
                # 迟到行补账（2026-11-03 z6-porter.it10，判据与落账见 eval_track）
                reconcile_late_eval_rows(
                    eval_jsonl, key16, it, pairs, lambda row: append_jsonl(eval_jsonl, row),
                    rollout_winrate, course_fp, _log,
                )
                return False
            # ---- 同族课程的现成读数（plan/episode-reuse.plan.md）----
            # 到兄弟课（同 level/关卡/难度/决策粒度）的 `eval_log.jsonl` 里找同 wver 同
            # (stage,seed) 的行：命中局**不进任务表**（连权重 POST / 节点往返都不发生），
            # 行照移植进本课账（下方「预落账」）——只替代**计算**，不替代账。
            todo, reused = reuse_eval_round(todo, key16=key16, args=args, it=it)
            t_eval_start = time.time()

            # 本地参与前提：冻结权重快照。主循环在 PPO 收尾后会原地覆盖 rl_path，
            # 本地局必须读派发时刻的 W(N)——赌时序读新权重 = 对账灾难。
            # 快照文件按评估流分流：it0 基线与 A-eval 可并发（基线落账前的重试轮
            # 撞上 eval 轮），同盘同名会互相覆写、后启动的本地局读到错权重。
            snapshot_path: str | None = None
            local_slots = max(0, int(policy.get("evalLocalSlots", EVAL_LOCAL_SLOTS_DEFAULT)))
            # 兜底模式的快照也必须在节点门之前建：rl_path 可能随后被 PPO 原地覆盖，
            # 不能等 nodes_ok 出来再读活指针。
            if (local_gate is not None or self.local_fallback_only) and local_slots > 0:
                try:
                    eval_dir.mkdir(parents=True, exist_ok=True)
                    snap = eval_dir / (
                        "_eval_frozen_weights-baseline.json"
                        if self.baseline
                        else "_eval_frozen_weights.json"
                    )
                    shutil.copyfile(rl_path, snap)
                    snapshot_path = str(snap)
                except OSError as e:
                    _log(f"[eval] WARN weights snapshot failed — local participation off: {e}")

            total = len(todo) + len(reused)  # 借来的局也进收工口径（行照落，只是不占节点）
            # 尾段预留量（公式在 worker/eval_yield.reserve_local_slots）：只在份额模式生效。
            # 兜底模式健康集群上本机不参与 ⇒ gate_wired=False ⇒ 恒 0，节点不白留队尾。
            reserved = reserve_local_slots(
                total,
                local_slots,
                snapshot_ready=snapshot_path is not None,
                gate_wired=not self.local_fallback_only and local_gate is not None,
            )
            soft_streak_max = int(policy.get("nodeSoftFailStreak", fail_streak_max * 3))
            # 收工即断连的作用域（线程 tag）：settled 满即置位 + 关在飞连接——尾部
            # 竞速副本/慢节点不再等（同 A/B 层）。**必须带课程身份**：同进程兄弟课程
            # 共享 RUN_ID ⇒ eval iterId 恒同，只用它会把另一门课的在飞请求一起掐死。
            req_scope = eval_request_scope(eval_iter_id, course_fp)
            common.distribution.clear_abort(req_scope)

            def _meta_ok(nid: str, manifest: dict, task, wall_sec, win: int) -> None:
                """成功局的采样机健康行。

                meta 是观测面：它**抛错不得让调用方重派**（成功边界 = eval 行 append，
                评审 P1-3）——否则同 (stage,seed) 会写第二行。`_record` 已兜住异常。
                """
                if nid == REUSE_NODE:  # 借来的局不是采样机，不进机器健康账
                    return
                _record_agent_meta(
                    meta_path,
                    {
                        "node": nid,
                        "mode": "eval",
                        "it": it,
                        "stage": task[0],
                        "seed": task[1],
                        "ok": True,
                        "win": win,
                        "elapsedSec": manifest.get("elapsedSec"),
                        "wallSec": wall_sec,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )

            def _record_failed_meta(nid: str, task, reason: str) -> None:
                try:
                    _record_agent_meta(
                        meta_path,
                        {
                            "node": nid,
                            "mode": "eval",
                            "it": it,
                            "stage": task[0],
                            "seed": task[1],
                            "ok": False,
                            "reason": reason,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                except Exception as e:
                    _log(
                        f"[eval] WARN record-failed meta 写入失败（忽略）"
                        f": {type(e).__name__}: {str(e)[:160]}"
                    )

            rnd = EvalRound(
                seq=arb.next_seq(),
                it=it,
                iter_id=iter_id,
                eval_iter_id=eval_iter_id,
                wver=wver,
                key16=key16,
                course_fp=course_fp,
                args=args,
                bun=bun,
                eval_dir=eval_dir,
                eval_jsonl=eval_jsonl,
                meta_path=meta_path,
                task_timeout=task_timeout,
                window=window,
                post_budget=post_budget,
                snapshot_path=snapshot_path,
                local_slots=local_slots,
                fallback_only=self.local_fallback_only,
                reserved=reserved,
                local_gate=local_gate,
                fail_streak_max=fail_streak_max,
                soft_streak_max=soft_streak_max,
                req_scope=req_scope,
                total=total,
                pairs=pairs,
                todo=todo,
                weights_kind=EVAL_WEIGHTS_KIND,
                weights_bytes=None,
                attempts_max=EVAL_TASK_ATTEMPTS,
                attempts_transient_max=EVAL_TASK_ATTEMPTS_TRANSIENT,
                record_retry_max=EVAL_RECORD_RETRY_MAX,
                reuse_node=REUSE_NODE,
                row_builder=eval_row,
                append_row=append_jsonl,
                meta_ok=_meta_ok,
                record_failed_meta=_record_failed_meta,
                fail_recorder=record_eval_failed,
                local_fn=run_local_eval_game,
                abort_scope=lambda: common.distribution.abort_active_requests(req_scope),
                log=_log,
            )
            arb.register(rnd)
            # 命中局预落账：行照落（进 `landed` 收工口径），但不占节点、不碰网络；
            # `seen` 同步登记，否则收官行会报「认领≠落盘」（那是给丢掉的真局用的告警）。
            rnd.land_reused(reused)
            # 份额模式：本机在节点门前先开工（旧派发器同序）；兜底模式等 nodes_ok 出来再定。
            if not self.local_fallback_only and snapshot_path is not None and local_slots > 0:
                execs.ensure_local(local_slots, owner=rnd)

            local_bun = bun_version(bun)
            # 2026-09-03 修正（mac 实测 stage.tiles null）：eval_stages 含自定义关
            # （>=2000）时，节点必须有能力位 stageJsonSupport——旧 agent（无该位）
            # 收到 stage=2000 会走 arena/真实关解析 → stage null → 崩溃。无能力节点
            # 一律跳过，任务自然落回本机 local（已支持 stage-json 透传）。
            need_sj = bool(todo) and any(t[0] >= 2000 for t in todo)
            # 节点门（2026-09-17 统一）：与 rollout 同一判据 = codeHash——唯一事实来源
            # tools/agent/codehash-files.txt（引擎 src/game、config、RNG、God AI 已并入
            # 该清单）。不再比 ping.engineEpoch：该字段已从 /v1/ping 移除（engine_epoch
            # 退为账本记录值），也不再需要「旧 agent 无字段→过渡期放行」的分支——
            # codeHash 是 rollout 门一直都在用的字段。
            code_hash_local = common.distribution.compute_code_hash()
            alive = []
            # 并行 ping（保序）：串行墙钟 = Σ 每台延迟（两台超时即 ~7s），而节点门每轮
            # 重跑一次；并行 == 最慢一台（同 batch_eval，2026-09-19 审计 B2）。
            # push 节点（`gpu_push` / `offline_push`）没有 `/v1/ping` ⇒ 不进评估候选
            # （否则每轮评估都在日志里留一条「ping 失败/超时」，见 `sampler_nodes`）。
            enabled_nodes = common.protocol.sampler_nodes(cfg.get("nodes", []))
            pings = common.distribution.ping_nodes_parallel(enabled_nodes, timeout=status_timeout)
            for n, ping in zip(enabled_nodes, pings, strict=True):
                nid = str(n.get("id") or n.get("url") or "?")
                if ping is None:
                    # 留痕：旧实现静默 continue——节点被丢时日志里既看不出是谁、也看不出
                    # 为什么（2026-09-19 审计 B4）。
                    _log(
                        f"[eval] node {nid}: ping 失败/超时（并行探测，预算 {status_timeout}s）"
                        f" — 本轮不参与"
                    )
                    continue
                if not ping.get("evalSupport"):
                    _log(
                        f"[eval] node {nid}: agent lacks evalSupport — skipped "
                        f"(sync code + restart agent to enable)"
                    )
                    continue
                if need_sj and not ping.get("stageJsonSupport"):
                    _log(
                        f"[eval] node {nid}: 自定义关 eval 需 stageJsonSupport 能力位"
                        f"（旧 agent）—— skipped，任务落本机 local"
                    )
                    continue
                # R2 事件 rung：事件课程的局只派给能力位节点（旧 agent 静默跑均匀局混入）。
                # 缺席 = 老课程不查（getattr 缺省 False）。
                if bool(getattr(args, "decision_events", False)) and not ping.get(
                    "decisionEventsSupport"
                ):
                    _log(
                        f"[eval] node {nid}: 事件课程 eval 需 decisionEventsSupport 能力位"
                        f"（旧 agent）—— skipped，任务落本机 local"
                    )
                    continue
                # 决策周期 K（plan/k5-rhythm.plan.md）：K≠10 课程的局同样只派给能力位节点
                # （旧 agent 静默跑 K=10 混入，同规 fail-closed；缺席/10 = 不查）。
                if int(getattr(args, "decision_k", 10)) != 10 and not ping.get("decisionKSupport"):
                    _log(
                        f"[eval] node {nid}: K≠10 课程 eval 需 decisionKSupport 能力位"
                        f"（旧 agent）—— skipped，任务落本机 local"
                    )
                    continue
                if mm(str(ping.get("bunVersion", "?"))) != mm(local_bun):
                    _log(f"[eval] node {nid}: bun version mismatch — skipped")
                    continue
                ch_why = common.distribution.check_code_hash(ping, code_hash_local)
                if ch_why:
                    _log(f"[eval] node {nid}: {ch_why} — skipped")
                    continue
                # 本轮（本 eval 轮）bootId 钉 + 核（与 rollout 同判据，按**腿**分账本：
                # 同一 iterId 的 eval 与 rollout 是两轮采集，不能共用一个键）。
                boot_why = node_identity.note_ping(
                    node_identity.round_key("eval", eval_iter_id), nid, ping
                )
                if boot_why:
                    _log(f"[eval] node {nid}: {boot_why} — skipped")
                    _record_agent_meta(
                        meta_path,
                        {
                            "node": nid,
                            "mode": "eval",
                            "it": it,
                            "ok": False,
                            "reason": boot_why,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                    )
                    continue
                base_c = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
                # 让位（2026-11-01，docs/nn/runtime-opt.md §39）：节点自报在飞 = 本课 rollout
                # （以及别的腿/课程）正占着的槽。eval 只拿剩下的，不把自己的 concurrency
                # 再加一遍；**不留 floor**（2026-11-03 用户口径「全力保证 rollout」）——
                # 占满时 c=0，本轮先不孵线程，等 `grow_pools` 在间隙里补。
                c_n = eval_slot_cap(base_c, int(ping.get("inflight") or 0))
                if c_n < base_c:
                    _log(
                        f"[eval] node {nid}: 让出在飞 {base_c - c_n} 槽 ⇒ c={c_n}/{base_c}"
                        f"（inflight={ping.get('inflight')}）"
                    )
                alive.append(
                    {
                        "id": nid,
                        "url": n["url"],
                        "key": n.get("authKey", ""),
                        "c": c_n,
                        "baseC": base_c,  # 扩容判据（间隙里 ping 空出的槽）
                    }
                )
            # 本机槽位可用 ⇔ snapshot_path 已建（见上方快照段）⇒ 门失败不再整轮空转。
            if not alive:
                if snapshot_path is None:
                    _log("[eval] no eval-capable node — skipped this round")
                    return False
                _log("[eval] no eval-capable node — local-only eval this round")

            # B2（2026-11-10）：冻结期不推权重。窗口钟已暂停，等本课解冻后再 POST；
            # 进程 shutdown 时放弃本轮。冻结可能发生在 ping 之后，故门放在读权重之前。
            if not arb.wait_for_thaw(course_fp):
                return False

            # 幂等下发（节点通常已持有 → kept 短路径；agent 重启过则补发）——并行
            # ★ 2026-10-15：与 rollout 腿同一口径——推之前先把 delta 容器落地成整份
            #   （评估节点自己也 `JSON.parse` 这个文件）。
            weights_standard.ensure_standard_weights(rl_path, log=_log)
            with open(rl_path, "rb") as f:
                weights_bytes = f.read()
            rnd.weights_bytes = weights_bytes

            def _on_weight_ready(nd: dict) -> None:
                """权重一到就开采（流水线；POST 池线程回调，须线程安全）。

                先 `activate` 再起线程：执行线程只从 `rnd.nodes` 里找节点上下文，
                线程先跑会拿到「node gone」。
                """
                rnd.activate(nd)
                execs.ensure_node(nd, int(nd.get("c") or 0), owner=rnd)

            # 读权重期间可能又进入冻结：POST 前再确认一次（窗口钟仍停）。
            if not arb.wait_for_thaw(course_fp):
                return False

            nodes_ok = (
                common.distribution.post_weights_parallel(
                    alive, iter_id, wver, weights_bytes, timeout=post_budget,
                    kind=EVAL_WEIGHTS_KIND, log=_log, on_alive=_on_weight_ready,
                )
                if alive else []
            )
            # 窗口从**权重就绪**起算（2026-11-03）：POST 期间 `_on_weight_ready` 已让先
            # 就绪的节点开跑（它们读临时上限），到这里改写为「此刻起一个完整窗口」。
            # 本课冻结中（rollout 活跃）则窗口钟一开始就停走；别的课采集不停本课的钟（§113）。
            rnd.arm_window(time.time(), paused=arb.frozen_for(course_fp))
            rnd.set_remote_ready(nodes_ok)
            if alive and not nodes_ok:
                # 旧实现此处无条件 return：本机槽位明明可用却整轮 0 局（审计 B5）。
                if snapshot_path is None:
                    _log("[eval] all weight POSTs failed — skipped this round")
                    return False
                _log("[eval] all weight POSTs failed — local-only eval this round")

            # 兜底模式的唯一本机入口：只有 `nodes_ok` 为空才孵化；健康集群本机 0 线程、
            # reserved=0，summary 不会出现 local。
            if self.local_fallback_only and nodes_ok:
                _log(
                    f"[eval] it{it}: remote-only（nodes={[n['id'] for n in nodes_ok]}）"
                    "——本机不参与"
                )
            elif self.local_fallback_only:
                if snapshot_path is None or local_slots <= 0:
                    _log(
                        "[eval] no usable eval node"
                        f"（snapshot={'yes' if snapshot_path else 'no'}, slots={local_slots}）"
                        " — skipped this round"
                    )
                    return False
                rnd.mark_local_spawned()
                execs.ensure_local(local_slots, owner=rnd)
                _log(
                    f"[eval] it{it}: local fallback ×{local_slots}"
                    "（no usable remote eval node，本机接盘）"
                )

            _log(
                f"[eval] it{it}: dispatch {total} greedy games "
                f"(corpus={len(pairs)}, done={len(pairs) - total})"
                + (" [it0 基线 · bc 权重]" if self.baseline else "")
                + f" -> {[(n['id'], n['c']) for n in nodes_ok]}"
                + (f" [local tail-reserved ×{reserved}]" if reserved else "")
            )

            # 收官 drain / 远端全员 mismatch 时：gate 若仍关着，本机线程会一直等到
            # 窗口到期才放行——终轮 eval 等 600s 却 0 局。没有节点可派时立刻开闸。
            # 兜底模式没有 gate，也没有「已孵化但等门」的本机线程 ⇒ 不调用。
            if not self.local_fallback_only and release_local_gate_if_starved(local_gate, nodes_ok):
                _log(
                    f"[eval] it{it}: no remote nodes — local_gate released immediately"
                    f"（local_slots={local_slots} snapshot={'yes' if snapshot_path else 'no'}）"
                )
            for nd in nodes_ok:
                # `_on_weight_ready` 已为「POST 期间就绪」的节点 activated + 起过池；
                # 这里补幂等一遍——`post_weights_parallel` 的 on_alive 只是流水线优化，
                # 不能把「节点可派」建立在它一定被回调上（替身/降级实现没有回调）。
                rnd.activate(nd)
                execs.ensure_node(nd, int(nd.get("c") or 0), owner=rnd)

            # —— 等收口（任务级仲裁：线程一直活着，收工只看这一轮的账）——
            # ① settled 满 → 立即收工（断连 + 拒发新请求，慢节点/竞速副本不再等）；
            # ② 窗口到期 → **有界**等窗口内的在飞局落账（在飞清空即走，兜底
            #    EVAL_INFLIGHT_GRACE_SEC），那些局有价值，但不再空等 4–76s；
            # ③ 消费者归零但**队列还有活** ⇒ 自救（2026-11-01，z3-p2002ctl it25/it50）：
            #    先中途本机兜底（冻结快照在派发前就建好了），再按退避让**软失败**停派的
            #    节点回场。旧实现在这里直接 break：一次 503 busy 拥堵就把 800 局里的 786
            #    局判死，而窗口还剩 1776s；等一等集群就空出来了，缺的只是「谁去再取一次」。
            reason = supervise_round(rnd, execs)
            rnd.stop()
            if reason != "settled":
                grace_end = time.time() + inflight_grace_cap(task_timeout)
                while time.time() < grace_end and not rnd.settled.is_set():
                    # 在飞清零 ≠ 已落账：settle 先摘 `inflight`、写手随后才登记 `landed`
                    # （高负载下这段能跨过收工快照 ⇒ summary 误报 lost）。两个计数都归零
                    # 才是「这局真的落完了」。
                    if (
                        rnd.inflight_len() <= 0
                        and rnd.writers_pending() <= 0
                    ) or not _pools_alive(rnd, execs):
                        break
                    rnd.settled.wait(0.2)
            # —— 收工自报（plan/eval-baseline-undispatched §2 P0-1）：原因 + 队列剩余 ——
            # `never_pairs` 只装 attempts==0 的局：`pending` 是**待办**队列，失败局会被
            # 放回去（重投回队）——把它算成「从未派出」会在有失败的轮里撒谎
            # （2026-10-08 评审 F1）。`left_pending` 照报全量（人读最顺），归因只认 `never_pairs`。
            stats = rnd.close()
            tail_race_grants = int(stats.get("tail_race_grants", 0) or 0)
            dup_games = int(stats.get("dup_games", 0) or 0)
            frozen_sec = float(stats.get("frozen_sec", 0.0) or 0.0)
            left_pending = int(stats["left_pending"])
            inflight_n = int(stats["inflight_n"])
            never_pairs = stats["never_pairs"]
            landed_close_n = len(stats["landed"])
            claimed_n = len(stats["claimed"])
            remain = rnd.deadline(time.time()) - time.time()
            never_n = len(never_pairs)
            # D3（§4.2）：单节点集中亏损 = 机器级故障。`lost` 与 `eval_track` 同源式子；
            # 归因只读 meta 的 `ok=false` 行（没有 meta 的丢局不计入 ⇒ 偏保守）。
            lost_n = max(0, total - landed_close_n - never_n)
            lost_per_node, lost_errnos = lost_by_node(meta_path, it) if lost_n else ({}, {})
            close_reason = eval_close_reason(
                reason == "settled",
                reason == "workers-gone",
                reason == "window",
                lost=lost_n,
                lost_per_node=lost_per_node,
                stopped_nodes=rnd.stopped_nodes(),
            )
            tail = (
                f"【{close_reason}：剩余待派 {left_pending}（从未派出 {never_n}）、"
                f"在飞 {inflight_n}、已落账 {landed_close_n}、窗口余 {remain:.0f}s】"
            )
            # settled 满 = 断连（收工只等「正在写行」的赢家落盘）。
            closing = common.distribution.abort_active_requests(req_scope)
            if close_reason == "settled":
                _log(
                    f"[eval] it{it}: settled 满（{landed_close_n}/{total}）— 断连 {closing} 条"
                    f"在飞连接 + 拒发新请求，立即收工（慢节点/竞速副本不再等）{tail}"
                )
            elif close_reason == "machine-fault":
                top_node, top_n = max(lost_per_node.items(), key=lambda kv: kv[1])
                top_errno = (
                    max(lost_errnos.items(), key=lambda kv: kv[1])[0] if lost_errnos else "-"
                )
                _log(
                    f"[eval] it{it}: 机器级故障收工 —— {top_node} 独占 {top_n}/{lost_n} 局"
                    f"（errno={top_errno}）；缺口不进本轮判决，收官 drain 会补"
                    f"— 断连 {closing} 条在飞连接{tail}"
                )
            elif close_reason == "workers-gone":
                _log(
                    f"[eval] it{it}: 收工（worker 线程归零，未全落盘 {landed_close_n}/{total}）"
                    f"— 断连 {closing} 条在飞连接（在途局丢弃，下次续跑）{tail}"
                )
            else:
                _log(
                    f"[eval] it{it}: 收工（窗口到期 {window - remain:.0f}s，"
                    f"未全落盘 {landed_close_n}/{total}）— 断连 {closing} 条在飞连接"
                    f"（在途局丢弃，下次续跑）{tail}"
                )
            if left_pending > 0:
                # 可读性行（**不是**验收通道：_log() 只在 prefix_scope 内镜像课程日志，
                # eval 跑在后台线程 ⇒ 落盘的验收面只有 summary 的 close_reason/left_pending，
                # 2026-10-08 评审 F10）。
                _log(
                    f"[eval] it{it}: WARN 有 {left_pending} 局未落盘 —— 从未派出 {never_n} 局"
                    f"（never-dispatched）、派出未落盘 {left_pending - never_n} 局（lost）"
                    f"—— 查 worker 退出原因（远端节点断连/线程异常），不是节点失败"
                )
            rnd.report_comebacks()
            if tail_race_grants or dup_games or frozen_sec:
                _log(
                    f"[eval] it{it}: waste tail_race={tail_race_grants} "
                    f"dup={dup_games} frozen={frozen_sec:.0f}s"
                )
            quiet_deadline = time.monotonic() + 1.0
            while time.monotonic() < quiet_deadline:
                if rnd.writers_pending() <= 0:
                    break
                time.sleep(0.002)
            pending_writers = rnd.writers_pending()
            if pending_writers > 0:
                _log(
                    f"[eval] it{it}: 收工仍有 {pending_writers} 个写行者未落盘"
                    f"（1s 预算用尽，行可能晚于本行 summary 落账）"
                )

            # —— 三本账收工行（G5，评审 P0-5）：认领/落盘两个口径必须可比 ——
            loaded = stats["landed"]
            gap_preview = [f"s{s}/seed{sd}" for (s, sd) in todo if (s, sd) not in loaded][:8]
            _log(
                f"[eval] it{it}: 派出={total} 认领={claimed_n} 落盘={landed_close_n} "
                f"缺口={total - landed_close_n}"
                + (f"（{'、'.join(gap_preview)}…）" if gap_preview else "")
            )
            if claimed_n != landed_close_n:
                _log(
                    f"[eval] it{it}: WARN 认领≠落盘（{claimed_n} vs {landed_close_n}）"
                    f"— 未落盘局带 missing 原因进 summary，收官 drain 可补"
                )

            # 课程血缘进 summary 行（门控趋势过滤；与派发同源、同一份读数）。
            settle_eval_summary(
                eval_jsonl=eval_jsonl,
                key16=key16,
                it=it,
                pairs=pairs,
                total=total,
                landed=loaded,
                wins=rnd.wins,
                cleared_total=rnd.cleared_total,
                outcomes=rnd.outcomes,
                node_games=rnd.node_games,
                # summary 与逐局行走同一个单写者（两轮重叠时也不互相覆盖）。
                append_row=lambda row: append_jsonl(eval_jsonl, row),
                t_eval_start=t_eval_start,
                rollout_winrate=rollout_winrate,
                # D14 课程血缘（门按 course_fp 过滤趋势行；无课程 = ""=不过滤）。
                course_fp=course_fp,
                # 2026-10-08（plan/eval-baseline-undispatched §2 P0-1/P0-2）：收工读数进 summary。
                # `left_pending` = 收工时的待办余量（含重投回队）；`left_pairs` = 真·从未派出
                # 的那批（attempts==0）——`never_dispatched`/`never-dispatched` 两个读面都只认它。
                left_pending=left_pending,
                left_pairs=never_pairs,
                close_reason=close_reason,
                tail_race_grants=tail_race_grants,
                dup_games=dup_games,
                frozen_sec=frozen_sec,
            )
            return False
        except Exception as e:
            _log(f"[eval] round error (ignored): {type(e).__name__}: {str(e)[:200]}")
            if rnd is not None:
                rnd.close()
            return False
        finally:
            if rnd is not None:
                arb.unregister(rnd)
                execs.release_owner(rnd)


def _pools_alive(rnd: EvalRound, execs) -> bool:
    """收盘宽限期里是否还有**任何**执行线程能把这批在飞局落账。

    宽限期已 `rnd.stop()`（不再发新牌）⇒ 不能用 `round_live`（它要求可路由）。这里只看
    线程还在不在：在飞局由它们执行，线程全死 = 再等也不会有行落盘。
    """
    for nid in rnd.node_ids():
        if execs.live(nid) > 0:
            return True
    return bool(execs.live("local"))


def dispatch_eval_round(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    args,
    cfg: dict,
    iter_id: str,
    it: int,
    rollout_winrate: float | None = None,
    local_gate: threading.Event | None = None,
    baseline: bool = False,
    local_fallback_only: bool = False,
) -> bool:
    """固定语料干净评估（阻塞版，调用方放后台线程跑）。任何失败只记日志，绝不抛出。

    薄包装：OO 实现在 EvalDispatcher（trainer/eval_dispatch.py 同模块——本地直跑
    runner 的 monkeypatch 需落在本模块全局名上，见 e2e/test_run_rl.py）。
    返回值保留旧形状（恒 False：任务级仲裁下整轮不再被 rollout 插队收手）。
    """
    return EvalDispatcher(
        bun, rl_path, traj_dir, args, cfg, iter_id, it,
        rollout_winrate, local_gate, baseline, local_fallback_only,
    ).run()


def dispatch_eval_bg(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    args,
    cfg: dict,
    iter_id: str,
    it: int,
    rollout_winrate: float | None = None,
    local_gate: threading.Event | None = None,
    baseline: bool = False,
    local_fallback_only: bool = False,
) -> threading.Thread:
    """后台派发一轮：线程**立即开跑**（轮间不再排队），rollout 只冻结它的新投递。

    返回的线程 join = 等到这一轮真正收工（`worker/eval_gate.py`）。
    """
    from worker.eval_gate import eval_gate

    name = f"eval-it{it}" + ("-baseline" if baseline else "")

    def _job() -> None:
        dispatch_eval_round(
            bun,
            rl_path,
            traj_dir,
            args,
            cfg,
            iter_id,
            it,
            rollout_winrate,
            local_gate,
            baseline,
            local_fallback_only,
        )

    return eval_gate().submit(_job, name=name)

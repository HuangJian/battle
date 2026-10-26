"""干净评估分发：固定语料贪心局（旁路，绝不拖垮训练主循环）。

时机由调用方决定（流式=派发队列清空时经 on_queue_drained → dispatch_eval_bg；
串行=rollout 返回后藏进 PPO 空窗）。本模块只管单轮评估的派发与对账——
OO 化（2026-09-02）：原 dispatch_eval_round 函数迁为 EvalDispatcher 类，
run() 内局部别名 + 闭包保持（行为逐字节不变）；执行面工具在 biz/eval_local.py，
让位/份额（尾巴）策略的判据与公式在 biz/eval_yield.py（S5 第十二刀，2026-09-27）。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import common.distribution
import common.protocol
from common.log import log
from remote import weights_standard
from trainer.dispatch_queue import _record_agent_meta, bun_version, mm
from trainer.queue_local import pick_race_target, register_inflight
from worker import node_identity
from worker.archive import find_archive_weights  # noqa: F401 — 兼容旧引用

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
from worker.eval_track import course_logger, lost_by_node

# 让位/份额（尾巴）策略在 `biz/eval_yield.py`（S5 第十二刀）：本派发器只消费判据——尾段预留量 /
# 宽限强制释放点 / 在飞落账宽限的**公式**也住那边（不再是内联表达式）。
from worker.eval_yield import (
    EVAL_LOCAL_SLOTS_DEFAULT,
    eval_close_reason,
    hold_for_local,
    inflight_grace_cap,
    local_release_due,
    next_eval_wait,
    release_local_gate_if_starved,
    requeue_backoff_sec,
    reserve_local_slots,
    select_delayed_eval_it,  # noqa: F401 — re-exported（旧模块成员，兼容外部引用）
)
from worker.sibling_reuse import REUSE_NODE, land_reused_eval, reuse_eval_round

#: 干净评估的权重 kind（B6，2026-09-19）：节点按 (kind, wver) 分桶缓存权重
#: （sampler-agent `weightsByKindSha`）。旧实现与训练 rollout 共用 'rollout' 桶 ⇒ 训练每轮
#: 刷权重与 eval 那份在同桶内互相驱逐/清场，eval 局随即 409「wver not cached here」
#: （2026-09-19 审计 A1/B6；客户端只能靠 409 自愈重发兜底）。独立 kind 后两条腿互不驱逐，
#: 且日志与落盘文件（`weights-eval-<sha16>.json`）一眼可分。
#:
#: 协议侧 kind 是不透明字符串（POST x-kind / GET X-Kind / task ?kind= 三处同源）⇒
#: 旧节点无需任何改动，本改动不触 codeHash（nn-training/** 不在 SSOT 内）。
EVAL_WEIGHTS_KIND = "eval"


def _record_failed_meta(
    meta_path: Path,
    it: int,
    task: tuple[int, int],
    node: str,
    err: str,
    errno_code: str = "",
    msg_head: str = "",
) -> None:
    """写一条 `ok=false` 的 meta（丢局/缺口原因）。

    plan/eval-gap-honesty D1：`reason` 取**头部**（errno + 首行）——旧的 `[-160:]` 只留栈尾，
    bun 的 `error: ENOENT … open '<path>'` 正好被切掉。`errno` / `msg_head` 是**纯新增键**，
    不参与任何公式（历史行读侧按 unknown 处理）。
    """
    _record_agent_meta(
        meta_path,
        {
            "node": node,
            "mode": "eval",
            "it": it,
            "stage": task[0],
            "seed": task[1],
            "ok": False,
            "reason": f"{errno_code}: {msg_head}" if msg_head else err,
            "errno": errno_code,
            "msg_head": msg_head,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


class EvalDispatcher:
    """固定语料干净评估的单轮派发器（阻塞版，调用方放后台线程跑）。

    任何失败只记日志，绝不抛出（run() 顶层吞异常）。

    节点门：enabled ∧ ping ∧ ping.evalSupport ∧ bun major.minor 一致——旧 agent 无
    能力声明即跳过（它会静默忽略 mode 参数把评估局跑成采样局），逐节点灰度点亮。

    本地参与有两种模式：

    * **份额模式**（`local_fallback_only=False`，缺省）：R6 语义——local_gate 由调用方在
      「训练侧已无梯度步可做」（PPO/采集收尾）时 set，本机槽位在节点门前先开工并预留
      队尾。手动 evalA / B/C / 旧调用方走这里。
    * **兜底模式**（`local_fallback_only=True`，in-loop）：只有本轮 **没有任何可用节点**
      （`nodes_ok` 为空：无 enabled 节点 / 全部 ping 或能力门失败 / 权重 POST 全败）才启动
      本机线程；健康集群上本机 0 线程、不预留队尾。local_gate 不参与。
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

    def run(self) -> None:
        """原 dispatch_eval_round 主体：run() 内局部别名；份额模式行为逐字节保留。"""
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
        # 嵌套函数（worker / local_worker / record）自然闭包到这个局部名。
        # 别叫 `log`：同名赋值会让模块全局变局部；`_log` 仍闭包到模块全局（tests patch `ed.log`）。
        _log = course_logger(str(getattr(args, "course", "") or ""), log)

        # D1b（§2b）：eval 的私有 scratch **不是** it<N>/——轮转删 it<N>/ 时不会带走在飞的评估。
        eval_dir = traj_dir.parent / f"_eval-{traj_dir.name}"
        try:
            policy = cfg.get("policy", {})
            status_timeout = float(policy.get("statusTimeoutSec", 3))
            task_timeout = float(policy.get("taskTimeoutSec", 900))
            fail_streak_max = int(policy.get("nodeFailStreak", 3))
            window = float(getattr(args, "eval_window_sec", 1500) or 1500)
            deadline = time.time() + window
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
                return
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
                return
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
            pending: deque[tuple[int, int]] = deque(todo)
            lock = threading.Lock()
            #: **认领集**：只防重复结算（tail-race 副本 `if task in seen` 的判据）——
            #: 位置不动（record() 之前）！下移会让 record 进行中的副本看不到认领 ⇒ 双计
            #: （2026-10-02 评审 P0-3；tests/test_eval_dispatch_resilience.py 的 fanout 用例钉住）。
            seen: set[tuple[int, int]] = set()
            #: **落盘集**：eval 行 append 成功才算（成功边界，plan/eval-final-round-and-dropped
            #: S2）。收工触发（`_settle_complete`）与 summary 口径都看它——「settled 满」不再
            #: 可能出现在行未落盘时（h4-aim-k25 it0 的「settled 满 200/200 + 落盘 192」矛盾）。
            landed: set[tuple[int, int]] = set()
            attempts: dict[tuple[int, int], int] = {}
            # 按需建键（节点集在本块之后才定向——并行 ping 的门在闭包之后，见下）；
            # 读取一律 .get(nid, 0)，写入才建键。
            streaks: dict[str, int] = {}
            # 瞬时（背压/瞬断）连续计数：与真故障分开（与 A 层 dispatch.py 同款）。
            # 瞬时错误不计节点失败 streak，但连续软失败有上界——否则隧道/集群
            # 整体脉搏时会一直空转到窗口到期。任一一局结算即清零。
            soft_streak_max = int(policy.get("nodeSoftFailStreak", fail_streak_max * 3))
            soft_streaks: dict[str, int] = {}
            wins = [0]
            cleared_total = [0]
            outcomes: dict[str, int] = {}
            node_games: dict[str, int] = {}  # 每节点实际结算的评估局数（summary 用）
            jsonl_lock = threading.Lock()
            # 2026-09-16：eval 与 rollout 同款 in-flight race——pending 清空后空槽
            # 复制其它节点在跑的尾局，先返回者记账、败者丢弃（不双计）。
            inflight: dict[tuple[int, int], int] = {}
            inflight_nodes: dict[tuple[int, int], set[str]] = {}
            all_done = threading.Event()
            # 收工即断连的作用域（线程 tag）：settled 满即置位 + 关在飞连接——尾部
            # 竞速副本/慢节点不再等（同 A/B 层）。同进程 rollout 的标签不同，不误伤。
            req_scope = f"evalscope:{eval_iter_id}"
            common.distribution.clear_abort()
            #: 正在写行的赢家数（record 期间 +1）：收工只等它们落盘。
            writers = [0]
            #: 消费线程数（孵化即 +1，线程退出 -1）。全退 = 再等也不会结算 ⇒ 收工
            #: （否则「任务全被 drop + 无在飞」时只能空等整个窗口，2026-09-19）。
            live_workers = [0]
            #: **per-leg** 尝试数（plan/eval-gap-honesty D2，评审 F2）：`attempts[task]` 是两条腿
            #: 共享的总数，且 `attempts[task] == 0` 是 never_dispatched 的判据（见收工段）⇒
            #: 拿总数当重试判据会让「换机器重跑」的第一次失败就被 drop。
            leg_tries: dict[tuple[tuple[int, int], str], int] = {}
            #: 瞬断回队的到期时刻（退避）。两条腿共用 pending ⇒ 也必须共用这张表，
            #: 否则退避中的局会被另一条腿立刻抢走，退避形同虚设。
            retry_at: dict[tuple[int, int], float] = {}
            #: 本机腿改派过的一次性标记（防改派-失败-改派成环；与 `seen` 同一把锁保护）。
            redispatched: set[tuple[int, int]] = set()
            #: 本机腿本轮停派（**共享**事件：`local_slots` 是 N 条线程，线程局部停不掉全部）。
            local_stopped = threading.Event()

            def _node_worker_alive() -> bool:
                """节点消费线程是否还有一条活着（改派只认这个，不认 `nodes_ok`）。

                `nodes_ok` 非空 ≠ 线程还在跑：`worker()` 在 pending 空且无在飞时会 `return`。
                给一个已退光的池 `appendleft` 只会把 dropped 变成 lost（评审 F2）。
                调用方持 `lock`（与 `threads` 的追赶无关：列表已建、线程已 start）。
                """
                return any(t.is_alive() and t.name != "eval-local" for t in threads)

            def _settle_complete() -> None:
                """**落盘**满：置收工位 + 断连（调用方持 lock；实现非阻塞）。

                由 `landed` 触发（plan/eval-final-round-and-dropped S2/G3）：认领满不算收工——
                有一局行没落盘就不算结算。
                """
                all_done.set()
                common.distribution.abort_active_requests(req_scope)

            def _pop_inflight(task: tuple[int, int], nd_id: str) -> None:
                if task in inflight:
                    inflight[task] -= 1
                    if inflight[task] <= 0:
                        inflight.pop(task, None)
                        inflight_nodes.pop(task, None)
                    else:
                        inflight_nodes.get(task, set()).discard(nd_id)

            def _clear_inflight(task: tuple[int, int]) -> None:
                inflight.pop(task, None)
                inflight_nodes.pop(task, None)

            def _take_ready_locked(*, for_local: bool = False) -> tuple[int, int] | None:
                """取一局**已到期**的任务（调用方必须持 `lock`）。

                退避中的局被轮到队尾再转一圈：至多 `len(pending)` 次循环、无分配
                （§14.1）。全部未到期 ⇒ None（调用方去睡，与「队列空」同样处理）。

                `for_local=True`（本机腿）额外跳过长 `redispatched` 的局：它们已经被改派给
                节点，本机不能再把它们抢回来（否则快本机每次都先失败 → 一手改派形同虚设，
                线上就是「改派了又立刻 drop」）。节点线程全退时不再跳过（否则没人接）。
                """
                if not pending:
                    return None
                now = time.time()
                node_live = _node_worker_alive()
                for _ in range(len(pending)):
                    t = pending.popleft()
                    if for_local and t in redispatched and node_live:
                        pending.append(t)
                        continue
                    due = retry_at.get(t)
                    if due is None or due <= now:
                        retry_at.pop(t, None)
                        return t
                    pending.append(t)
                return None

            def record(
                manifest: dict,
                nd_id: str,
                task: tuple[int, int],
                wall_sec: float | None = None,
                row: dict | None = None,
            ) -> None:
                # 行构造的唯一实现点在 biz/eval_local.eval_row（云机离线评估用同一份）；
                # `row` 非空 = 兄弟课那一行的移植（读数逐字段来自源行，不重算）。
                row = row or eval_row(
                    manifest, it=it, key16=key16, task=task, node=nd_id, wall_sec=wall_sec
                )
                # 累加器要的两个量从行里读回（它们本来就是同一份 manifest 的派生）
                win = int(row["win"])
                cleared = int(row["cleared"])
                with jsonl_lock:
                    with open(eval_jsonl, "a", encoding="utf-8") as jf:
                        jf.write(json.dumps(row) + "\n")
                    # 采样机健康账本同册入账（mode:"eval"）——成功局才记，与 rollout 口径一致。
                    # meta 是观测面：它**抛错不得让调用方重派**（成功边界 = 上面的 eval 行
                    # append，评审 P1-3）——否则同 (stage,seed) 会写第二行。
                    try:
                        if nd_id != REUSE_NODE:  # 借来的局不是采样机，不进机器健康账
                            _record_agent_meta(
                                meta_path,
                                {
                                    "node": nd_id,
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
                    except Exception as e:
                        _log(
                            f"[eval] WARN record meta 写入失败（忽略）"
                            f": {type(e).__name__}: {str(e)[:160]}"
                        )
                with lock:
                    wins[0] += win
                    cleared_total[0] += cleared
                    node_games[nd_id] = node_games.get(nd_id, 0) + 1
                    oc = str(manifest.get("outcome"))
                    outcomes[oc] = outcomes.get(oc, 0) + 1

            def _record_with_retry(
                manifest: dict,
                nd_id: str,
                task: tuple[int, int],
                wall_sec: float | None,
                row: dict | None = None,
            ) -> bool:
                """落盘重试（G4）：成功边界 = eval 行 append 成功。

                本线程原地重试（总尝试 = 1 + EVAL_RECORD_RETRY_MAX）——**不回 pending**
                （回队会撞上 all_done/worker 已退的竞态，且重跑整局代价大）。仍失败 ⇒
                `record-failed` meta + WARN，本局不进 `landed`（计入缺口），由收官 drain
                按归档权重补（plan/eval-final-round-and-dropped S2）。
                """
                last_err: Exception | None = None
                for attempt in range(1, 2 + EVAL_RECORD_RETRY_MAX):
                    try:
                        record(manifest, nd_id, task, wall_sec, row=row)
                        break
                    except Exception as e:  # eval_row 缺键 / 写盘失败（行未落盘）
                        last_err = e
                        _log(
                            f"[eval] s{task[0]}/seed{task[1]} 落盘失败"
                            f"（attempt {attempt}/{1 + EVAL_RECORD_RETRY_MAX}）"
                            f": {type(e).__name__}: {str(e)[:160]}"
                        )
                else:
                    err_txt = (
                        f"{type(last_err).__name__}: {str(last_err)[:160]}"
                        if last_err is not None
                        else "unknown"
                    )
                    try:
                        _record_agent_meta(
                            meta_path,
                            {
                                "node": nd_id,
                                "mode": "eval",
                                "it": it,
                                "stage": task[0],
                                "seed": task[1],
                                "ok": False,
                                "reason": f"record-failed: {err_txt}",
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            },
                        )
                    except Exception as e2:
                        _log(
                            f"[eval] WARN record-failed meta 写入失败（忽略）"
                            f": {type(e2).__name__}: {str(e2)[:160]}"
                        )
                    _log(
                        f"[eval] s{task[0]}/seed{task[1]} 落盘失败 ×{1 + EVAL_RECORD_RETRY_MAX}"
                        f" —— dropped（本局计入缺口，收官 drain 可补）"
                    )
                    return False
                with lock:
                    landed.add(task)
                    if len(landed) >= total:
                        _settle_complete()
                return True

            # 命中局预落账：行照落（进 `landed` 收工口径），但不占节点、不碰网络；
            # `seen` 同步登记，否则收官行会报「认领≠落盘」（那是给丢掉的真局用的告警）。
            land_reused_eval(reused, _record_with_retry, seen, lock)

            def worker(nd: dict) -> None:
                # 线程本地标签（不继承主线程）：收工断连的作用域键。
                common.distribution.set_request_tag(req_scope)
                while time.time() < deadline and not all_done.is_set():
                    task = None
                    fanout_copy = False
                    t_task_start: float | None = None
                    leg = 0
                    with lock:
                        if (
                            streaks.get(nd["id"], 0) >= fail_streak_max
                            or soft_streaks.get(nd["id"], 0) >= soft_streak_max
                        ):
                            return
                        # 尾段预留：gate 未放行且余量 ≤ reserved 时不取（留给本机直跑）；
                        # 宽限期（local_release_due）强制释放防挂死。hold 中仍可对 in-flight race。
                        hold = hold_for_local(
                            len(pending),
                            reserved,
                            local_gate is not None and local_gate.is_set(),
                            local_release_due(time.time(), deadline),
                        )
                        if not hold:
                            got = _take_ready_locked()
                            if got is not None:
                                task = got
                                attempts[task] = attempts.get(task, 0) + 1
                                leg = leg_tries.get((task, nd["id"]), 0) + 1
                                leg_tries[(task, nd["id"])] = leg
                                register_inflight(inflight, task)
                                t_task_start = time.time()
                                inflight_nodes.setdefault(task, set()).add(nd["id"])
                            elif not pending and not inflight:
                                return
                        if task is None and inflight:
                            cand = pick_race_target(inflight, nd["id"], inflight_nodes, {})
                            if cand is not None:
                                task = cand
                                inflight[task] += 1
                                t_task_start = time.time()
                                inflight_nodes.setdefault(task, set()).add(nd["id"])
                                fanout_copy = True
                                leg = leg_tries.get((task, nd["id"]), 0) + 1
                                leg_tries[(task, nd["id"])] = leg
                                _log(
                                    f"[eval] tail-race s{task[0]}/seed{task[1]} "
                                    f"node={nd['id']} — race lane"
                                )
                    if task is None:
                        time.sleep(next_eval_wait(tuple(pending), retry_at, deadline, time.time()))
                        continue
                    ok = False
                    err = ""
                    transient = (
                        False  # 背压/瞬断（判据单一实现：common.distribution.is_transient_error）
                    )
                    task_lost = (
                        False  # 取包丢失（节点重启/清场；common.distribution.is_task_lost_error）
                    )
                    manifest: dict = {}
                    try:
                        from worker.config import args_rollout_overrides, stage_json_for_args

                        _ov = args_rollout_overrides(args)
                        manifest, _files = common.distribution.fetch_task(
                            nd["url"],
                            nd["key"],
                            iter_id=eval_iter_id,
                            wver=wver,
                            stage=task[0],
                            seed=task[1],
                            max_ticks=args.max_ticks,
                            difficulty=args.difficulty,
                            timeout=task_timeout,
                            mode="eval",
                            kind=EVAL_WEIGHTS_KIND,
                            # D14 课程血缘进任务键（agent taskKey 的 `:c<fp16>` 后缀）：
                            # 兄弟课程同 (stage,seed) 不再互串结果缓存（2026-10-08）。
                            course_fp=course_fp,
                            stage_json=stage_json_for_args(args, task[0]) or "",
                            # R2 事件 rung：与训练 rollout 同语义（缺席 = 老行为）。
                            decision_events=bool(getattr(args, "decision_events", False)),
                            # 决策周期 K：与训练 rollout 同语义（缺席/10 = 老行为）。
                            decision_k=int(getattr(args, "decision_k", 10)),
                            lives_override=int(_ov["lives_override"])
                            if "lives_override" in _ov
                            else None,
                            player_level=int(_ov["player_level"])
                            if "player_level" in _ov
                            else None,
                        )
                        why = common.distribution.validate_eval_result(manifest, wver)
                        if why:
                            raise common.distribution.DistError(0, why)
                        ok = True
                    except Exception as e:
                        err = str(e)[:200]
                        # 503 busy / 502 隧道 / 10054 / 超时 = 可恢复，**不计节点故障**
                        # （判据单一实现：common.distribution.is_transient_error；旧实现对 153 次
                        # 503 也记 streak、3 次即把该节点熔断整轮）。
                        transient = common.distribution.is_transient_error(e)
                        # 409 wver-not-cached = 可刷新条件：节点侧那份（归档）权重没了，
                        # 就地重发 + 清 reuse 缓存，同一节点继续用（不是节点故障）。
                        if (
                            isinstance(e, common.distribution.DistError)
                            and e.status == 409
                            and common.distribution.refresh_weights(
                                nd,
                                iter_id=eval_iter_id,
                                wver=wver,
                                weights_bytes=weights_bytes,
                                timeout=min(300.0, max(60.0, task_timeout)),
                                kind=EVAL_WEIGHTS_KIND,
                                err=err,
                                log=_log,
                            )
                        ):
                            transient = True
                        # 404「task lost on node」= 节点重启/清场把它进程内的结果/在飞任务清掉了
                        # （2026-09-19 审计 F1，与 A 层同判据）：不计故障、不耗 attempt 配额，
                        # 并清该节点这条腿的 reuse 账本（重启后桶也可能空了）。
                        task_lost = common.distribution.is_task_lost_error(e)
                        if task_lost:
                            common.distribution.forget_weights_node(
                                nd["id"], kind=EVAL_WEIGHTS_KIND
                            )
                            transient = True
                    with lock:
                        if ok:
                            if task in seen:
                                _pop_inflight(task, nd["id"])
                                _log(
                                    f"[eval] dup settle s{task[0]}/seed{task[1]} "
                                    f"node={nd['id']} — dropped"
                                )
                                continue
                            seen.add(task)
                            writers[0] += 1  # 收工前必须等它落盘
                            _clear_inflight(task)
                            streaks[nd["id"]] = 0
                            soft_streaks[nd["id"]] = 0
                            # 收工触发**不在这里**：认领≠落盘；`_settle_complete` 由 landed 在
                            # record 成功后触发（G3，评审 P0-3）。
                        elif fanout_copy:
                            _pop_inflight(task, nd["id"])
                        else:
                            # 瞬时错误不计节点击败（与 A 层 / 一次性评估同判据），但连续
                            # 软失败仍有上界——否则整体脉停（隧道挂了）时会一直空转到窗口到期。
                            if transient:
                                soft_streaks[nd["id"]] = soft_streaks.get(nd["id"], 0) + 1
                                if soft_streaks[nd["id"]] == soft_streak_max:
                                    _log(
                                        f"[eval] node {nd['id']}: 连续 {soft_streak_max} 次瞬时失败"
                                        f"（背压/瞬断，非节点故障）— 本轮停派"
                                    )
                            else:
                                streaks[nd["id"]] = streaks.get(nd["id"], 0) + 1
                            _pop_inflight(task, nd["id"])
                            if (
                                leg < EVAL_TASK_ATTEMPTS
                                or (transient and leg < EVAL_TASK_ATTEMPTS_TRANSIENT)
                            ) and task not in seen:
                                if transient:
                                    retry_at[task] = time.time() + requeue_backoff_sec(leg)
                                pending.append(task)
                                _log(
                                    f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued"
                                    + (
                                        "（任务丢失·节点重启，已回队、不计故障）"
                                        if task_lost
                                        else "（瞬断/背压，不计节点故障）"
                                        if transient
                                        else ""
                                    )
                                )
                            elif task not in seen:
                                _record_failed_meta(meta_path, it, task, nd["id"], err)
                                _log(
                                    f"[eval] s{task[0]}/seed{task[1]} failed {leg}x ({err}) "
                                    f"— dropped"
                                )
                    if ok:
                        wall_sec = (
                            round(time.time() - t_task_start, 3)
                            if t_task_start is not None
                            else None
                        )
                        record_ok = False
                        try:
                            record_ok = _record_with_retry(manifest, nd["id"], task, wall_sec)
                        finally:
                            with lock:
                                writers[0] = max(0, writers[0] - 1)
                        if not record_ok:
                            continue  # 失败已 WARN + record-failed meta；本局计入缺口
                        el = manifest.get("elapsedSec")
                        _log(
                            f"[eval] {len(landed)}/{total} s{task[0]}/seed{task[1]} "
                            f"node={nd['id']} outcome={manifest.get('outcome')} "
                            f"ticks={manifest.get('ticks')} "
                            f"elapsed={str(el) + 's' if el is not None else '-'}"
                        )

            def local_worker() -> None:
                """本机直跑 worker：gate 放行前让位训练（每 5s 醒来看一眼 deadline）。"""
                if snapshot_path is None:
                    return
                while time.time() < deadline and not all_done.is_set():
                    if local_stopped.is_set():
                        return  # 连续瞬断熔断：N 条本机线程共享同一个事件
                    t_task_start = None
                    if not self.local_fallback_only and local_gate is not None and not local_gate.is_set():
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return
                        if not local_gate.wait(timeout=min(5.0, remaining)):
                            continue
                    task = None
                    fanout_copy = False
                    leg = 0
                    with lock:
                        got = _take_ready_locked(for_local=True)
                        if got is not None:
                            task = got
                            attempts[task] = attempts.get(task, 0) + 1
                            leg = leg_tries.get((task, "local"), 0) + 1
                            leg_tries[(task, "local")] = leg
                            register_inflight(inflight, task)
                            t_task_start = time.time()
                            inflight_nodes.setdefault(task, set()).add("local")
                        elif not pending and not inflight:
                            return
                        if task is None and inflight:
                            cand = pick_race_target(inflight, "local", inflight_nodes, {})
                            if cand is not None:
                                task = cand
                                inflight[task] += 1
                                t_task_start = time.time()
                                inflight_nodes.setdefault(task, set()).add("local")
                                fanout_copy = True
                                leg = leg_tries.get((task, "local"), 0) + 1
                                leg_tries[(task, "local")] = leg
                                _log(
                                    f"[eval] tail-race s{task[0]}/seed{task[1]} "
                                    f"node=local — race lane"
                                )
                    if task is None:
                        time.sleep(min(1.0, max(0.1, deadline - time.time())))
                        continue
                    ok = False
                    err = ""
                    # D1（§2.1）：失败保真——`LocalEvalProcessError` 带 errno/首行/头尾；
                    # 其余异常退回 `str(e)`（本机腿也会抛 DistError/JSON 错）。
                    err_errno = ""
                    err_msg_head = ""
                    transient = False
                    manifest: dict = {}
                    try:
                        from worker.config import args_rollout_overrides, stage_json_for_args

                        _ov = args_rollout_overrides(args)
                        manifest = run_local_eval_game(
                            bun,
                            snapshot_path,
                            task[0],
                            task[1],
                            eval_dir / "local-eval" / f"rl_s{task[0]}_seed{task[1]}",
                            max_ticks=args.max_ticks,
                            difficulty=args.difficulty,
                            timeout_sec=task_timeout,
                            wver=wver,
                            stage_json=stage_json_for_args(args, task[0]) or "",
                            lives_override=int(_ov["lives_override"])
                            if "lives_override" in _ov
                            else None,
                            player_level=int(_ov["player_level"])
                            if "player_level" in _ov
                            else None,
                            # R2 事件 rung：本机直跑与节点同语义（缺席 = 老行为）。
                            decision_events=bool(getattr(args, "decision_events", False)),
                            # 决策周期 K：本机直跑与节点同语义（缺席/10 = 老行为）。
                            decision_k=int(getattr(args, "decision_k", 10)),
                        )
                        why = common.distribution.validate_eval_result(manifest, wver)
                        if why:
                            raise common.distribution.DistError(0, why)
                        ok = True
                    except Exception as e:
                        err = str(e)[:200]
                        err_errno = str(getattr(e, "errno_code", "") or "")
                        err_msg_head = str(getattr(e, "msg_head", "") or "")[:160]
                        # 判据单一实现：A/B/C 三层共用 `common.distribution.is_transient_error`。
                        transient = common.distribution.is_transient_error(e)
                    with lock:
                        if ok:
                            if task in seen:
                                _pop_inflight(task, "local")
                                _log(
                                    f"[eval] dup settle s{task[0]}/seed{task[1]} "
                                    f"node=local — dropped"
                                )
                                continue
                            seen.add(task)
                            writers[0] += 1  # 收工前必须等它落盘
                            _clear_inflight(task)
                            # 任一局结算即清零（与节点腿同款）："连续"才是熔断判据，
                            # 否则成败交替的抖动会把计数攒到 soft_streak_max 而误停派。
                            soft_streaks["local"] = 0
                            # 收工触发**不在这里**：认领≠落盘；见 `_record_with_retry`。
                        elif fanout_copy:
                            _pop_inflight(task, "local")
                        else:
                            _pop_inflight(task, "local")
                            if transient:
                                # 瞬断不计节点故障，但连续软失败有上界（与节点腿同款）——
                                # 否则持续机器故障会一直空转到窗口到期。
                                soft_streaks["local"] = soft_streaks.get("local", 0) + 1
                                if soft_streaks["local"] == soft_streak_max:
                                    local_stopped.set()
                                    _log(
                                        f"[eval] local: 连续 {soft_streak_max} 次瞬时失败"
                                        "（背压/瞬断，非节点故障）— 本轮停派"
                                    )
                                if leg < EVAL_TASK_ATTEMPTS_TRANSIENT and task not in seen:
                                    retry_at[task] = time.time() + requeue_backoff_sec(leg)
                                    pending.append(task)
                                    _log(
                                        f"[eval] s{task[0]}/seed{task[1]} failed ({err})"
                                        " — requeued（瞬断/背压，不计节点故障）"
                                    )
                                elif task not in seen:
                                    _record_failed_meta(
                                        meta_path,
                                        it,
                                        task,
                                        "local",
                                        err,
                                        err_errno,
                                        err_msg_head,
                                    )
                                    _log(
                                        f"[eval] s{task[0]}/seed{task[1]} failed {leg}x ({err})"
                                        " — dropped（瞬断预算用尽）"
                                    )
                            elif task not in seen and _node_worker_alive():
                                # 非瞬断（真失败）：先换机器重跑，不在坏机器上重试（D2-5）。
                                # 一手性由**_取任务侧**保证（`_take_ready_locked(for_local=True)`
                                # 跳过长 `redispatched` 的局）——节点线程不会复活，所以本机不会
                                # 把已改派的局抢回来失败再改派（再判一次 `task not in redispatched`
                                # 是死代码，评审时特意去掉；改派一手性用例钉的是取任务侧那条 skip）。
                                redispatched.add(task)
                                pending.appendleft(task)
                                _log(
                                    f"[eval] s{task[0]}/seed{task[1]} failed ({err})"
                                    f" — 改派给在线节点（本机第 {leg} 次）"
                                )
                            elif leg < EVAL_TASK_ATTEMPTS and task not in seen:
                                pending.append(task)
                                _log(f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")
                            elif task not in seen:
                                _record_failed_meta(
                                    meta_path,
                                    it,
                                    task,
                                    "local",
                                    err,
                                    err_errno,
                                    err_msg_head,
                                )
                                _log(
                                    f"[eval] s{task[0]}/seed{task[1]} failed {leg}x ({err})"
                                    " — dropped"
                                )
                    if ok:
                        wall_sec = (
                            round(time.time() - t_task_start, 3)
                            if t_task_start is not None
                            else None
                        )
                        record_ok = False
                        try:
                            record_ok = _record_with_retry(manifest, "local", task, wall_sec)
                        finally:
                            with lock:
                                writers[0] = max(0, writers[0] - 1)
                        if not record_ok:
                            continue  # 失败已 WARN + record-failed meta；本局计入缺口
                        el = manifest.get("elapsedSec")
                        _log(
                            f"[eval] {len(landed)}/{total} s{task[0]}/seed{task[1]} "
                            f"node=local outcome={manifest.get('outcome')} "
                            f"ticks={manifest.get('ticks')} "
                            f"elapsed={str(el) + 's' if el is not None else '-'}"
                        )

            # —— 份额模式：本机槽位先开工（不等节点门/权重门，审计 B3）——
            # 本地权重就是本机冻结快照，无需下发；节点门（并行 ping）与权重 POST 都是
            # 秒级开销，让本机槽位干等纯属白丢吞吐。兜底模式没有确定要跑的本机槽位，
            # 必须等 `nodes_ok` 出来再孵化（健康集群上本机 0 线程）。
            def _spawn_tracked(fn, *a: Any, name: str = "") -> threading.Thread:
                """孵化消费线程并计入 live_workers（线程必须先计数再启动：主线程在
                spawn 完才看计数，晚计数会让收工判断误以为「消费线程全退」）。"""
                live_workers[0] += 1

                def _body() -> None:
                    try:
                        fn(*a)
                    finally:
                        with lock:
                            live_workers[0] -= 1

                t = threading.Thread(target=_body, daemon=True, name=name)
                t.start()
                return t

            threads: list[threading.Thread] = []
            # 份额模式：本机在节点门前先开工；兜底模式等 nodes_ok 出来再决定。
            if not self.local_fallback_only and snapshot_path is not None and local_slots > 0:
                for _ in range(local_slots):
                    threads.append(_spawn_tracked(local_worker, name="eval-local"))

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
            #（否则每轮评估都在日志里留一条「ping 失败/超时」，见 `sampler_nodes`）。
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
                c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
                alive.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
            # 本机槽位可用 ⇔ snapshot_path 已建（见上方快照段）⇒ 门失败不再整轮空转。
            if not alive:
                if snapshot_path is None:
                    _log("[eval] no eval-capable node — skipped this round")
                    return
                _log("[eval] no eval-capable node — local-only eval this round")

            # 幂等下发（节点通常已持有 → kept 短路径；agent 重启过则补发）——并行
            # ★ 2026-10-15：与 rollout 腿同一口径——推之前先把 delta 容器落地成整份
            #   （评估节点自己也 `JSON.parse` 这个文件）。
            weights_standard.ensure_standard_weights(rl_path, log=_log)
            with open(rl_path, "rb") as f:
                weights_bytes = f.read()
            nodes_ok = (
                common.distribution.post_weights_parallel(
                    alive,
                    iter_id,
                    wver,
                    weights_bytes,
                    timeout=min(300.0, max(60.0, task_timeout)),
                    kind=EVAL_WEIGHTS_KIND,
                    log=_log,
                )
                if alive
                else []
            )
            if alive and not nodes_ok:
                # 旧实现此处无条件 return：本机槽位明明可用却整轮 0 局（审计 B5）。
                if snapshot_path is None:
                    _log("[eval] all weight POSTs failed — skipped this round")
                    return
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
                    return
                for _ in range(local_slots):
                    threads.append(_spawn_tracked(local_worker, name="eval-local"))
                _log(
                    f"[eval] it{it}: local fallback ×{local_slots}"
                    "（no usable remote eval node）"
                )

            _log(
                f"[eval] it{it}: dispatch {total} greedy games "
                f"(corpus={len(pairs)}, done={len(pairs) - total})"
                + (" [it0 基线 · bc 权重]" if self.baseline else "")
                + f" -> {[(n['id'], n['c']) for n in nodes_ok]}"
                + (f" [local tail-reserved ×{reserved}]" if reserved else "")
            )

            # 收官 drain / 远端全员 mismatch 时：gate 若仍关着，local_worker 会空等到
            # deadline 才放行——终轮 eval 等 600s 却 0 局。没有节点可派时立刻开闸。
            # 兜底模式没有 gate，也没有「已孵化但等门」的本机线程 ⇒ 不调用。
            if not self.local_fallback_only and release_local_gate_if_starved(local_gate, nodes_ok):
                _log(
                    f"[eval] it{it}: no remote nodes — local_gate released immediately"
                    f"（local_slots={local_slots} snapshot={'yes' if snapshot_path else 'no'}）"
                )
            for nd in nodes_ok:
                for _ in range(nd["c"]):
                    threads.append(_spawn_tracked(worker, nd, name=f"eval-{nd['id']}"))
            # —— 等收口（**显式**：旧实现靠 join(window + task_timeout) 顺带完成）——
            # ① settled 满 → 立即收工（req 4：断连 + 拒发新请求，慢节点/竞速副本不再等）；
            # ② 墙钟到期 → **有界**等窗口内的在飞局落账（在飞清空即走，兜底
            #    EVAL_INFLIGHT_GRACE_SEC），那些局有价值，但不再像旧实现那样空等 4–76s。
            while not all_done.is_set() and time.time() < deadline:
                all_done.wait(0.2)
                with lock:
                    if live_workers[0] <= 0:
                        break
            grace_end = time.time() + inflight_grace_cap(task_timeout)
            while not all_done.is_set() and time.time() < grace_end:
                with lock:
                    if not inflight or live_workers[0] <= 0:
                        break
                all_done.wait(0.2)
            # —— 收工自报（plan/eval-baseline-undispatched §2 P0-1）：原因 + 队列剩余 ——
            # 旧收工行只说「未全落盘 N/M」——it0 那 95 局在 21s/1500s 收工（worker 线程归零），
            # 日志层却报不出「哪条路径收的工、队列还剩多少」，所以 ④（线程为什么退光）无从查。
            # `never_pairs` 只装 attempts==0 的局：`pending` 是**待办**队列，失败局会被
            # `pending.append` 放回去（重投回队）——把它算成「从未派出」会在有失败的轮里撒谎
            # （2026-10-08 评审 F1）。`left_pending` 照报全量（人读最顺），归因只认 `never_pairs`。
            with lock:
                no_consumers = live_workers[0] <= 0
                left_pending = len(pending)
                inflight_n = len(inflight)
                spawned_n = len(threads)
                never_pairs = {p for p in pending if attempts.get(p, 0) == 0}
                landed_close_n = len(landed)
            remain = deadline - time.time()
            # D3（§4.2）：单节点集中亏损 = 机器级故障。`lost` 与 `eval_track` 同源式子；
            # 归因只读 meta 的 `ok=false` 行（没有 meta 的丢局不计入 ⇒ 偏保守）。
            lost_n = max(0, total - landed_close_n - len(never_pairs))
            lost_per_node, lost_errnos = lost_by_node(meta_path, it) if lost_n else ({}, {})
            stopped_nodes = {k for k, v in soft_streaks.items() if v >= soft_streak_max}
            if local_stopped.is_set():
                stopped_nodes.add("local")
            close_reason = eval_close_reason(
                all_done.is_set(),
                no_consumers,
                time.time() >= deadline,
                lost=lost_n,
                lost_per_node=lost_per_node,
                stopped_nodes=stopped_nodes,
            )
            never_n = len(never_pairs)
            tail = (
                f"【{close_reason}：剩余待派 {left_pending}（从未派出 {never_n}）、"
                f"在飞 {inflight_n}、已起线程 {spawned_n}、窗口余 {remain:.0f}s】"
            )
            # settled 满 = 断连（收工只等「正在写行」的赢家落盘）。
            closing = common.distribution.abort_active_requests(req_scope)
            if all_done.is_set():
                _log(
                    f"[eval] it{it}: settled 满（{len(landed)}/{total}）— 断连 {closing} 条"
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
                    f"[eval] it{it}: 收工（worker 线程归零，未全落盘 {len(landed)}/{total}）"
                    f"— 断连 {closing} 条在飞连接（在途局丢弃，下次续跑）{tail}"
                )
            else:
                _log(
                    f"[eval] it{it}: 收工（窗口到期 {window - remain:.0f}s，"
                    f"未全落盘 {len(landed)}/{total}）— 断连 {closing} 条在飞连接"
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
            all_done.set()
            quiet_deadline = time.monotonic() + 1.0
            while time.monotonic() < quiet_deadline:
                with lock:
                    if writers[0] <= 0:
                        break
                time.sleep(0.002)
            with lock:
                pending_writers = writers[0]
            if pending_writers > 0:
                _log(
                    f"[eval] it{it}: 收工仍有 {pending_writers} 个写行者未落盘"
                    f"（1s 预算用尽，行可能晚于本行 summary 落账）"
                )
            # 线程只做 best-effort 收拢（0.25s 总预算）：daemon 线程本就随进程退出。
            join_deadline = time.monotonic() + 0.25
            for t_ in threads:
                t_.join(timeout=max(0.01, join_deadline - time.monotonic()))

            # —— 三本账收工行（G5，评审 P0-5）：认领/落盘两个口径必须可比 ——
            with lock:
                claimed_n = len(seen)
                landed_n = len(landed)
                gap_preview = [f"s{s}/seed{sd}" for (s, sd) in todo if (s, sd) not in landed][:8]
            _log(
                f"[eval] it{it}: 派出={total} 认领={claimed_n} 落盘={landed_n} "
                f"缺口={total - landed_n}"
                + (f"（{'、'.join(gap_preview)}…）" if gap_preview else "")
            )
            if claimed_n != landed_n:
                _log(
                    f"[eval] it{it}: WARN 认领≠落盘（{claimed_n} vs {landed_n}）"
                    f"— 未落盘局带 missing 原因进 summary，收官 drain 可补"
                )

            # 课程血缘进 summary 行（门控趋势过滤；与派发同源、同一份读数）。
            settle_eval_summary(
                eval_jsonl=eval_jsonl,
                key16=key16,
                it=it,
                pairs=pairs,
                total=total,
                landed=landed,
                wins=wins,
                cleared_total=cleared_total,
                outcomes=outcomes,
                node_games=node_games,
                jsonl_lock=jsonl_lock,
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
            )
        except Exception as e:
            _log(f"[eval] round error (ignored): {type(e).__name__}: {str(e)[:200]}")


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
) -> None:
    """固定语料干净评估（阻塞版，调用方放后台线程跑）。任何失败只记日志，绝不抛出。

    薄包装：OO 实现在 EvalDispatcher（trainer/eval_dispatch.py 同模块——本地直跑
    runner 的 monkeypatch 需落在本模块全局名上，见 e2e/test_run_rl.py）。
    """
    EvalDispatcher(
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
    t_ = threading.Thread(
        target=dispatch_eval_round,
        args=(
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
        ),
        daemon=True,
        name=f"eval-it{it}" + ("-baseline" if baseline else ""),
    )
    t_.start()
    return t_

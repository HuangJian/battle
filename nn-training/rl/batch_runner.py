"""batch_runner — B 层单元执行器（`BatchEvalRunner` / `dispatch_batch_bg`）。

2026-09-25（S27/B3）从 `rl/batch_eval.py` **纯搬**出来（成员逐字节对账见
`tests/test_batch_runner_split.py`）—— 那是 §5.5.4 的第三步：B1 把纯规划/判据送到
`rl/batch_plan.py`、B2 把台账收进 `rl/batch_store.py`，本模块收走**执行面**。
一个单元 = 一个 100 局批次：通道机器（每节点各自 ping+权重就绪即派单）· 尾段竞速 ·
背压重排 · 收尾三闸（settled / 僵死 / 零消费者宽限）。

**这个模块的边**：只向下（`dist_common` / `rl.{batch_plan,batch_store,eval_local,log,queue,queue_local}`），
**不** import 旧家 `rl.batch_eval`（否则成环）；`rl.batch_eval` 反过来再导出本模块的公开名。

**★ 注入点（patch 目标）**：本模块的依赖注入靠**模块全局**，测试 `monkeypatch.setattr` 打的是
**读它的那个模块**：
  * `rl.batch_runner.log`（单元日志）· `rl.batch_runner.bun_version`（节点版本门）·
    `rl.batch_runner.run_local_eval_game`（本机槽位）—— 这三处原打在 `rl.batch_eval.*`，
    B3 随执行器改址（打在旧家 = **静默空操作**，名字还在但没人读）。
  * `rl.batch_runner.STUCK_GRACE_SEC` 一类常量同理：它们**只被本模块读**，故**不再经旧家转发**。

**关键契约**（原文照搬，别在本模块里改语义）：
  - 节点门（§6.6）：enabled ∧ ping ∧ evalSupport ∧ stageJsonSupport ∧ bunVersion 一致 ∧
    **codeHash 一致**（= rollout 门同一判据）——**严格拒派，不静默降级**（P2 DoD）。
  - iterId 命名空间 `{runId}.b{batchShort}u{unit}`：agent taskKey 无 policy 分量，
    命名空间隔离是 god/nn 不串键的唯一保证。
  - 窗口（§6.5）：只在窗口开时派新局；在途局自然跑完（taskTimeoutSec 封顶）；
    剩余局按 batch_unit 下窗续跑（`units.done` 落 `batches.jsonl` 台账，见 `rl/batch_store.py`）。
  - 确定性契约（§3.4/P2 DoD）：同 ckpt + 同 seed 集 + 同 stage ⇒ gameplay 字段逐字节一致
    （node/elapsedSec/phase/ts 不要求）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import NamedTuple

import dist_common
from rl.batch_plan import (
    batch_iter_id,
    is_transient_error,
    kind_for_policy,
    node_gate_reason,
)
from rl.batch_store import BatchStore, data_root
from rl.eval_local import (
    EVAL_LOCAL_SLOTS_DEFAULT,
    EVAL_TASK_ATTEMPTS,
    eval_census_fields,
    eval_loot_fields,
    eval_v8_fields,
    run_local_eval_game,
)
from rl.log import log
from rl.queue import _record_agent_meta, bun_version

# 尾竞速：与 A 层（rl/eval_dispatch）**同一机制**（纯函数在 rl/queue_local 单源）。
from rl.queue_local import (
    clear_inflight,
    pick_race_target,
    pop_inflight,
    register_inflight,
)

#: 背压退避封顶（秒）。指数序列 0.25/0.5/1/2/4/8 覆盖 6 次重排。
BUSY_BACKOFF_CAP_SEC = 8.0
#: 收尾僵死兜底（2026-09-19）：队列空 + 无在飞 + settled 不再变化持续这么久 ⇒ 认定有局在
#: 重试配额耗尽后被丢弃（收尾条件 `len(seen) >= total` 永不成立），立即响亮收工，
#: 而不是干等到 deadline。取 10s：足够容纳回包 / 背压重排 / 节点重探的间隙。
STUCK_GRACE_SEC = 10.0


#: 失联/未就绪节点的重探间隔（秒）—— 用户 2026-09-19 第 5 条：「失联的节点，每 20 秒
#: ping 一次，ping 通了就立即传权重派任务」。旧实现只在**单元开头**探一次：一个单元内
#: 节点恢复也永远等不到活（一次性评估 = 一个单元 ⇒ 整批都等不到）。
#: `policy.recoverPingSec` 可覆盖（单测用极小值）。
RECOVER_PING_SEC = 20.0
#: 「零消费者」宽限（秒）：没有任何节点就绪、本机槽位也 0 时，等多久才判定整批无人可跑
#: （响亮收摊，不无限等）。有本机槽位时不适用——本地永远是消费者 ⇒ 节点可无限重探。
NO_CONSUMER_GRACE_SEC = 180.0
#: 单节点「恢复后仍 0 局成功」的最大轮次（`policy.nodeRecoveryTries` 可覆盖）——
#: 有进展即清零，见 BatchEvalRunner.mark_tripped。
NODE_RECOVERY_TRIES = 3


def _heartbeat(**patch: object) -> None:
    """R4-G1：写 EvalBoard 心跳（失败静默——心跳绝不打断单元）。"""
    try:
        from rl.eval_heartbeat import write_state

        write_state(**patch)
    except Exception:
        pass


class _UnitPlan(NamedTuple):
    """一个单元的**派发前置条件** = `_open_unit` 的产出 = 通道机器与收尾要用的一切。

    单元开头内部的中间量（`policy_cfg` / `window` / `god` / `iter_base` / `pairs` /
    `done_before` / `snapshot_path`）**不外漏** —— 它们是那句话的局部推导，写进契约只会让
    「机器到底依赖什么」变模糊。字段与 `_open_unit` 的返回**逐名对应**（守卫钉住）。
    """

    # ── 任务语义（来自 unit）──
    unit: dict
    kind: str
    unit_lives: int | None
    unit_level: int | None
    stage_params: dict
    total: int
    todo: list[tuple[int, int]]
    t_start: float
    # ── 三个超时 + 背压/重探配额（policy 配置）──
    status_timeout: float
    task_timeout: float
    fail_streak_max: int
    busy_retry_limit: int
    busy_backoff_sec: float
    recover_ping_sec: float
    no_consumer_grace: float
    max_recovery_tries: int
    # ── 身份与门：iterId / 权重指纹 / 本机 bun / codeHash ──
    iter_id: str
    weights_bytes: bytes
    wver: str
    key16: str
    local_bun: str
    code_hash_local: str
    # ── 派发面 ──
    enabled_nodes: list[dict]
    local_slots: int
    local_weights: str | None
    local_on: bool
    deadline: float
    trace: bool
    req_scope: str


class BatchEvalRunner:
    """一批次中一个 100 局单元的派发器（阻塞版，调用方放后台线程跑）。

    任何失败只记日志，绝不抛出。窗口关闭（window_event 未置位且超时）即停派
    新局，在途局自然收完；剩余 seed 下窗续跑（units.done 已落台账）。
    """

    def __init__(
        self,
        bun: str,
        rl_path: str | None,
        eval_log: Path,
        args,
        cfg: dict,
        batch: dict,
        unit: dict,
        unit_idx: int,
        unit_of: int,
        run_id: str,
        engine_epoch: str,
        policy: str = "nn",
        window_event: threading.Event | None = None,
        init_sha16: str = "",
        kind: str | None = None,
        include_scorable: bool = False,
    ) -> None:
        self.bun = bun
        self.rl_path = rl_path
        self.eval_log = eval_log
        self.args = args
        self.cfg = cfg
        self.batch = batch
        self.unit = unit
        self.unit_idx = unit_idx
        self.unit_of = unit_of
        self.run_id = run_id
        self.engine_epoch = engine_epoch
        self.policy = policy
        self.window_event = window_event
        self.init_sha16 = init_sha16
        # kind：None = 由 policy 推（见 KIND_FOR_POLICY）；显式给值只为一处例外——
        # 一次性评估入口要复用既有 agent 桶命名时。
        self.kind = kind or kind_for_policy(policy)
        # include_scorable：把 agent 报告的原始 `scorable`（scoreV7 的完整输入：
        # finalState + telemetry）原样落到逐局行。默认关 ⇒ A/B/C 层的行**逐字节不变**；
        # eval_m1_once 打开它，把 TS 侧的打分输入原样带回（不做字段级搬运 = 不会漂移）。
        self.include_scorable = include_scorable

    def run(self) -> dict:
        try:
            return self._run()
        except Exception as e:
            log(f"[batcheval] unit error (ignored): {type(e).__name__}: {str(e)[:200]}")
            return {"settled": 0, "total": 0, "dropped": 0}

    def _run(self) -> dict:
        """一个单元 = **开头 → 通道机器 → 收尾**（三段各自成方法，见下面三个被调者）。

        这一段只讲相位；判断与状态都在被调者里。`_open_unit` 用返回 dict 表示「无事可做」
        （nn 缺权重 / 本单元已全结算），直接透传。
        """
        plan = self._open_unit()
        if isinstance(plan, dict):
            return plan
        return self._run_channels(plan)

    def _open_unit(self) -> _UnitPlan | dict:
        """单元开头：把配置 / 权重 / 语料 / 通道表**算清楚**，返回一个单元的全部前置条件。

        两种「没事干」直接返回结果 dict（调用方透传）：
          - nn 单元没有权重（`rl_path` 空）⇒ 0 局；
          - 本单元的 pair 全都已结算（`_done_keys`）⇒ settled=total，不重复跑。
        """

        args = self.args
        unit = self.unit
        policy_cfg = self.cfg.get("policy", {})
        status_timeout = float(policy_cfg.get("statusTimeoutSec", 3))
        task_timeout = float(policy_cfg.get("taskTimeoutSec", 900))
        fail_streak_max = int(policy_cfg.get("nodeFailStreak", 3))
        # 背压参数（与 rl/bc_dispatch 的 busy 重排同源，默认值取同一量级）：
        # 节点满负荷时回 503 busy / 连接被重置（10054）——是**限流信号不是故障**。
        # 原实现把它计入 nodeFailStreak：一套被训练作业占满的集群会在 1 秒内把
        # 6 个节点全部熔断（2026-09-19 实测 x20-powered it0 探针 200 局 **全部**
        # 落本地，单元墙钟 172–191s，日志里只有十几行 “requeued”）。
        busy_retry_limit = int(policy_cfg.get("busyRetryLimit", 6))
        busy_backoff_sec = float(policy_cfg.get("busyBackoffSec", 0.25))
        window = float(getattr(args, "eval_window_sec", 1500) or 1500)
        deadline = time.time() + window
        god = self.policy == "god"
        if god:
            # god 局无权语义，但 agent 的 /v1/task **一律**按 (kind, wver) 查缓存桶
            #（无 god 豁免；weightsOf 是全量 sha 精确查表，2026-09-19 核实）⇒
            # 必须 POST 占位 `{}` 并把它的 sha 当 wver 传。key16 仍是行身份（显示/去重）。
            weights_bytes = b"{}"
            wver = hashlib.sha256(weights_bytes).hexdigest()
            key16 = f"god-{self.engine_epoch[:12]}"
        else:
            if not self.rl_path:
                log("[batcheval] nn unit without weights — skipped")
                return {"settled": 0, "total": 0, "dropped": 0}
            wver = dist_common.weights_fingerprint(self.rl_path)
            key16 = wver[:16]
            with open(self.rl_path, "rb") as f:
                weights_bytes = f.read()
        iter_base = batch_iter_id(self.run_id, str(self.batch.get("batch_id")))
        iter_id = f"{iter_base}u{self.unit_idx}"
        kind = self.kind
        # unit 可**缺** lives/level：缺 = 不做覆盖（difficulty / 关卡默认说了算）。
        # ladder/corpora 的 unit 恒带值（旧行为逐字不变）；一次性评估工具跑内置关时
        # 没有课程覆盖语义，传 3 之类会把「难度默认」硬编码成常数（改难度即错）。
        unit_lives = None if unit.get("lives") is None else int(unit["lives"])
        unit_level = None if unit.get("level") is None else int(unit["level"])
        #: 多关单单元（一次性评估）的逐关参数表；缺省空 ⇒ 全部回落 unit 级字段。
        stage_params: dict = unit.get("stageParams") or {}
        #: 失联重探间隔（用户第 5 条：每 20s ping 一次，通了就立即传权重派单）。
        recover_ping_sec = float(policy_cfg.get("recoverPingSec", RECOVER_PING_SEC))
        #: 「零消费者」宽限（秒）——**只在整批从未有过任何消费者时生效**：没有任何节点
        #: 就绪过 + 本机槽位 0 ⇒ 等到这个上限就响亮 deferred（不无限等）。曾就绪过的节点
        #: 掉线后仍按 recover_ping_sec 一直重探到窗口截止（用户第 5 条）。
        no_consumer_grace = float(policy_cfg.get("noConsumerGraceSec", NO_CONSUMER_GRACE_SEC))
        #: 单节点「恢复后仍 0 局成功」的最大轮次：连续这么多次就认定它是**坏的**而不是
        #: 一时失联，本单元不再等它（其余节点/本机槽位继续）。有进展（结算过任意一局）
        #: 就把轮次清零 —— 用户第 5 条的「失联重探」是给抖动/重启用的，不是给死节点。
        max_recovery_tries = int(policy_cfg.get("nodeRecoveryTries", NODE_RECOVERY_TRIES))
        #: 收工断连的作用域键（只关本单元自己的在飞请求——rollout 同进程并发，不能误伤）。
        req_scope = f"batcheval:{iter_id}"
        # 新单元开跑：解除上一单元可能的收工态（否则本单元请求落地就被拒）。
        dist_common.clear_abort()
        # 单 unit 跨多关（一次性评估：用户 2026-09-19 第 2 条「不要 u0/u1/u2 阶段」）：
        # unit["pairs"] = [[stageId, seed], …] 时以它为准；缺省仍由 seeds × stageId 展开
        #（ladder/corpora 的单元逐字不变）。逐关参数走 unit["stageParams"]，见 params_for。
        if unit.get("pairs"):
            pairs = [(int(p[0]), int(p[1])) for p in unit["pairs"]]
        else:
            seeds = [int(s) for s in unit["seeds"]]
            pairs = [(int(unit["stageId"]), s) for s in seeds]
        total = len(pairs)
        done_before = self._done_keys(key16)
        todo = [p for p in pairs if p not in done_before]
        if not todo:
            log(f"[batcheval] {unit['rung']} u{self.unit_idx}: already settled — skip")
            return {"settled": total, "total": total, "dropped": 0}
        t_start = time.time()
        trace = dist_common.trace_enabled()
        # R4-G1 心跳：单元开始（console 只读，显示当前批/单元/rung）。
        _heartbeat(
            window_open=(
                self.window_event.is_set() if self.window_event is not None else True
            ),
            batch_id=str(self.batch.get("batch_id")),
            unit_idx=self.unit_idx,
            unit_of=self.unit_of,
            rung=str(unit.get("rung", "")),
            remaining_units=max(0, self.unit_of - self.unit_idx),
            engine_epoch=self.engine_epoch,
        )

        snapshot_path: str | None = None
        local_slots = max(0, int(policy_cfg.get("evalLocalSlots", EVAL_LOCAL_SLOTS_DEFAULT)))
        if not god and self.rl_path and local_slots > 0:
            try:
                snap = self.eval_log.parent / f"_batcheval_frozen_{self.unit_idx}.json"
                shutil.copyfile(self.rl_path, str(snap))
                snapshot_path = str(snap)
            except OSError as e:
                log(f"[batcheval] WARN weights snapshot failed — local off: {e}")
        # god 局不读权重文件（export 侧 weightsText='{}'）：无 rl_path 时占位串即可
        # （从不读取）；nn 局必须读派发时刻冻结快照。
        local_weights = snapshot_path or ((self.rl_path or "no-weights-god") if god else None)

        local_bun = bun_version(self.bun)
        # 节点门指纹（2026-09-17 统一）：与 rollout 门同源 = SSOT 清单的 codeHash。
        code_hash_local = dist_common.compute_code_hash()
        # ---- 派发通道：**每节点各自就绪、各自派单**（用户 2026-09-19 五条裁定）----
        # 旧实现是三个**串行阶段**：ping 全部节点 → 全部节点收权重 → 才开派。慢节点把整批
        # 拖住（实测 gate 6.83s + weights 0.79s，**且每个单元重跑一遍**），而**已经就绪的
        # 节点在这段时间里空转**（用户：「一个节点权重分发成功后立即派发任务」）。现在每个
        # 节点一条通道：ping 通 → 传权重 → **立刻**派活，不等任何别的节点；就绪的节点不再
        # 被 ping、不再重传同一份权重；失联节点每 recover_ping_sec 重探，通了立即派单。
        enabled_nodes = [n for n in self.cfg.get("nodes", []) if n.get("enabled", True)]
        # 本机槽位不需要任何门/权重 ⇒ 与节点通道并行、**立刻**开工。
        local_on = local_weights is not None and local_slots > 0
        log(
            f"[batcheval] {unit['rung']} u{self.unit_idx}/{self.unit_of} "
            f"policy={self.policy}: dispatch {len(todo)} games -> "
            f"{len(enabled_nodes)} 节点通道（各自 ping+权重就绪即派单，不等慢节点）"
            + (f" + local ×{local_slots}" if local_on else "")
        )

        # god 的占位 `{}` 权重也在各自通道内 POST（不 POST 则 /v1/task 必然 409 ——
        # 这正是 C 层 god 批此前「无节点可用」表象的真因）。
        assert weights_bytes is not None
        return _UnitPlan(
            unit=unit,
            kind=kind,
            unit_lives=unit_lives,
            unit_level=unit_level,
            stage_params=stage_params,
            total=total,
            todo=todo,
            t_start=t_start,
            status_timeout=status_timeout,
            task_timeout=task_timeout,
            fail_streak_max=fail_streak_max,
            busy_retry_limit=busy_retry_limit,
            busy_backoff_sec=busy_backoff_sec,
            recover_ping_sec=recover_ping_sec,
            no_consumer_grace=no_consumer_grace,
            max_recovery_tries=max_recovery_tries,
            iter_id=iter_id,
            weights_bytes=weights_bytes,
            wver=wver,
            key16=key16,
            local_bun=local_bun,
            code_hash_local=code_hash_local,
            enabled_nodes=enabled_nodes,
            local_slots=local_slots,
            local_weights=local_weights,
            local_on=local_on,
            deadline=deadline,
            trace=trace,
            req_scope=req_scope,
        )

    def _run_channels(self, plan: _UnitPlan) -> dict:
        """通道机器：**每节点一条通道**（就绪即派单 / 失联重探）+ 本机槽位 + 尾段竞速。

        机器本体（状态 + 11 个方法 + 主循环）住 `_UnitLanes`；本方法只剩「构造 → 跑」。
        它**不**独立成模块：机器体读本模块的依赖注入锚点（见 `_UnitLanes` docstring）。
        """
        return _UnitLanes(self, plan).run()

    def _settle_unit(
        self,
        *,
        lock: threading.Lock,
        seen: set[tuple[int, int]],
        nodes_ready_ever: set[str],
        writers: list[int],
        dup_settles: list[int],
        stop_watch: threading.Event,
        all_done: threading.Event,
        node_games: dict[str, int],
        node_soft_fails: dict[str, int],
        node_hard_fails: dict[str, int],
        threads: list[threading.Thread],
        unit: dict,
        req_scope: str,
        todo: list[tuple[int, int]],
        t_start: float,
    ) -> dict:
        """收尾三闸：停机 → 断连 → 只等**正在写行的赢家**落盘 → 线程 best-effort → 落账。

        这里是「一次批单元」对台账的唯一交代：`dropped == 0` ⇒ `mark_unit_done`（含
        `node_dist` 参与度）；否则 `reopen_for_resume`（部分完成，下窗续跑）。两者都在
        `try` 里 —— B 层的规矩是**任何失败只记日志、绝不抛出**（训练主链零风险）。
        """

        stop_watch.set()
        # req 4：收工即断连（settled 满或墙钟到点都一样——在途局结果无用，不许再拖着等）。
        closing = dist_common.abort_active_requests(req_scope)
        if all_done.is_set():
            log(
                f"[batcheval] u{self.unit_idx} settled 满 → 断连 {closing} 条在飞连接 + 拒发新请求，"
                f"立即收工"
            )
        else:
            log(
                f"[batcheval] u{self.unit_idx} 收工（未全结算）→ 断连 {closing} 条在飞连接"
                f"（在途局丢弃，下次 idle 窗续跑）"
            )
        all_done.set()  # 单元收尾（在途竞速副本/慢节点连接已断，结果本就无用）
        # 收工只等一件事：**正在写行的赢家**落盘（<=1s 兜底，实际微秒级）。慢节点/竞速
        # 副本的回包永不需要（结果无用，行也不会写），等它们纯属浪费——实测那 5s 全是
        # 「等 dup settle 回包」，占 800 局墙钟的 3%（2026-09-19 用户追问）。
        quiet_deadline = time.monotonic() + 1.0
        while time.monotonic() < quiet_deadline:
            with lock:
                if writers[0] <= 0:
                    break
            time.sleep(0.002)
        # 线程只做 best-effort 收拢（0.25s 总预算）：daemon 线程本就随进程退出，
        # 训练循环里它们也会在下一次 fetch 返回时自行退出（all_done 已在循环顶判）。
        join_deadline = time.monotonic() + 0.25
        for t_ in threads:
            t_.join(timeout=max(0.01, join_deadline - time.monotonic()))
        # dropped = todo 中未结算（断点续跑由 units.done + eval_done_keys 双保险）
        dropped = len(todo) - len(seen)
        log(
            f"[batcheval] {unit['rung']} u{self.unit_idx} DONE settled={len(seen)}/{len(todo)} "
            f"dropped={dropped} dup={dup_settles[0]} sec={round(time.time() - t_start, 1)}"
        )
        self._log_provenance(
            unit=unit,
            node_games=node_games,
            node_soft_fails=node_soft_fails,
            node_hard_fails=node_hard_fails,
            nodes_ready_ever=nodes_ready_ever,
        )
        try:
            if dropped == 0:
                BatchStore(data_root()).mark_unit_done(
                    str(self.batch.get("batch_id")), self.unit_idx, dict(node_games)
                )
            else:
                # 部分完成（yield/超时）：不标 unit done，批回 pending 供 idle 续跑；
                # 已结算 seed 由 _done_keys 跳过，不重复计。
                log(
                    f"[batcheval] {unit['rung']} u{self.unit_idx}: partial "
                    f"({dropped} left) — reopen batch for resume"
                )
                BatchStore(data_root()).reopen_for_resume(str(self.batch.get("batch_id")))
        except Exception as e:
            log(f"[batcheval] WARN mark_unit_done failed: {e}")
        # R4-G1 心跳：单元结束（清 rung；window_open 留给 loop_core 的开关窗写点）。
        _heartbeat(
            batch_id=str(self.batch.get("batch_id")), rung=None, remaining_units=0
        )
        return {"settled": len(seen), "total": len(todo), "dropped": dropped}

    def _log_provenance(
        self,
        *,
        unit: dict,
        node_games: dict[str, int],
        node_soft_fails: dict[str, int],
        node_hard_fails: dict[str, int],
        nodes_ready_ever: set[str],
    ) -> None:
        """参与度账（provenance）：**谁跑的必须自证**。

        逐局行带的 `node` 列是同一份账的落盘形态；这里在日志里再算一遍，让「熔断静默降
        本地」不再可能被误读为分布式。两条响亮告警分别对应「远端 0 参与但仍跑成」与
        「本单元 0 局跑成」（后者见 2026-09-19 那次权重桶被收敛扫掉的实测）。
        """

        # 参与度账（provenance）：谁跑的必须自证。逐局行带的 node 列是同一份账的
        # 落盘形态；这里在日志里再算一遍，让“熔断静默降本地”不再可能被误读为分布式。
        tally = ", ".join(f"{k}={v}" for k, v in sorted(node_games.items())) or "none"
        remote = sum(v for k, v in node_games.items() if k != "local")
        local = node_games.get("local", 0)
        if node_soft_fails or node_hard_fails:
            soft = ", ".join(
                f"{k}={v}" for k, v in sorted(node_soft_fails.items())
            ) or "—"
            hard = ", ".join(
                f"{k}={v}" for k, v in sorted(node_hard_fails.items())
            ) or "—"
            log(f"[batcheval] {unit['rung']} u{self.unit_idx} 背压计数 {soft}｜真失败计数 {hard}")
        log(f"[batcheval] {unit['rung']} u{self.unit_idx} provenance: {tally}")
        if remote == 0 and len(nodes_ready_ever) > 0:
            if local > 0:
                log(
                    f"[batcheval] ⚠ u{self.unit_idx} 远端 0 参与（{len(nodes_ready_ever)} 个就绪节点全部失败/"
                    f"停派）——本单元实际 **全本地** 跑：逐局口径不变（本地与节点引擎已对账），"
                    f"但墙钟慢约 10x；确认集群是否被训练作业占满（/v1/ping + 节点 inflight）"
                )
            else:
                # 2026-09-19 实测：训练作业每轮重写 'rollout' 桶 ⇒ 评估权重被节点侧
                # 同 kind 保留份数收敛扫掉，但内存桶仍说 cached ⇒ 任务子进程 ENOENT，
                # client 只见 10054，重试耗尽 ⇒ 单元 0 局。本机槽位 0 时整批无行退出。
                log(
                    f"[batcheval] ⚠ u{self.unit_idx} 本单元 **0 局跑成**（{len(nodes_ready_ever)} 个就绪节点"
                    f"全部失败，本机槽位 0）——若集群上有训练作业在跑：节点侧那份权重文件可能被"
                    f"同 kind 保留份数收敛扫掉（workdir-cleanup.WEIGHT_FILES_KEEP）；查节点日志的"
                    f"ENOENT 与 tmp/dist-agent/weights-*.json，一次性评估用专用 kind 规避"
                )


    def _done_keys(self, key16: str) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        try:
            if self.eval_log.exists():
                for line in self.eval_log.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(r, dict) and r.get("event") == "eval" and r.get("wver") == key16:
                        try:
                            out.add((int(r["stage"]), int(r["seed"])))
                        except (KeyError, TypeError, ValueError):
                            continue
        except OSError:
            pass
        return out


class _UnitLanes:
    """一个单元的**通道机器**（原 `_run_channels` 体内：状态 + 11 个闭包 + 起跑 + 主循环）。

    `self.*` = 本次单元运行的状态；执行器在 `self.owner`。依赖注入（`log` / `bun_version` /
    `run_local_eval_game` / `STUCK_GRACE_SEC`）仍按**本模块**全局解析 ⇒ 那些
    `monkeypatch.setattr("rl.batch_runner.X")` 的锚点**不迁移**（这是本类不单独出模块的理由）。
    """

    def __init__(self, owner: BatchEvalRunner, plan: _UnitPlan) -> None:
        self.owner = owner
        # 契约字段逐名取一次（`_UnitPlan` 是唯一来源）：名字写错不会静默换值，
        # 少取一个 ⇒ 下面任意方法一跑就 AttributeError。
        self.unit = plan.unit
        self.kind = plan.kind
        self.unit_lives = plan.unit_lives
        self.unit_level = plan.unit_level
        self.stage_params = plan.stage_params
        self.total = plan.total
        self.todo = plan.todo
        self.t_start = plan.t_start
        self.status_timeout = plan.status_timeout
        self.task_timeout = plan.task_timeout
        self.fail_streak_max = plan.fail_streak_max
        self.busy_retry_limit = plan.busy_retry_limit
        self.busy_backoff_sec = plan.busy_backoff_sec
        self.recover_ping_sec = plan.recover_ping_sec
        self.no_consumer_grace = plan.no_consumer_grace
        self.max_recovery_tries = plan.max_recovery_tries
        self.iter_id = plan.iter_id
        self.weights_bytes = plan.weights_bytes
        self.wver = plan.wver
        self.key16 = plan.key16
        self.local_bun = plan.local_bun
        self.code_hash_local = plan.code_hash_local
        self.enabled_nodes = plan.enabled_nodes
        self.local_slots = plan.local_slots
        self.local_weights = plan.local_weights
        self.local_on = plan.local_on
        self.deadline = plan.deadline
        self.trace = plan.trace
        self.req_scope = plan.req_scope

        self.pending: deque[tuple[int, int]] = deque(self.todo)
        self.lock = threading.Lock()
        self.seen: set[tuple[int, int]] = set()
        self.attempts: dict[tuple[int, int], int] = {}
        self.streaks: dict[str, int] = {}
        #: 逐节点通道账（诊断 + 单测断言）：ping 次数 / 就绪时刻 / 本单元曾就绪过的节点。
        self.node_pings: dict[str, int] = {}
        self.node_ready_at: dict[str, float] = {}
        self.nodes_ready_ever: set[str] = set()
        #: 通道表 + 当前就绪通道数（>0 = 有节点在干活）。
        self.lanes: dict[str, dict] = {}
        self.ready_lanes = [0]
        self.settled = [0]
        #: 事件追踪（用户 2026-09-19：「CPU 满一阵又掉档几十秒」——需要看**在飞数**
        #: 随时间的变化，才能分清「排队空了（尾巴）」与「派发被阻塞（阶段）」）。
        self.in_flight = [0]
        #: 正在落盘逐局行的赢家数。收工**只等它归零**（微秒级）：settled 一满时可能还有
        #: 一个赢家在写自己那行（它在置位 all_done 之前已进 record），不等就会丢行。
        #: 而慢节点/竞速副本的回包**一律不用等**——那些行永不需要（结果本就无用）。
        self.writers = [0]
        #: 竞速败者/收尾副本的丢弃计数（收尾汇总一行，替代赛后逐条刷屏）。
        self.dup_settles = [0]
        self.stop_watch = threading.Event()
        #: 全部任务已结算 → worker 立即收工（尾段竞速副本不再空等）。
        self.all_done = threading.Event()
        #: in-flight 副本账（与 A 层同机制）：task → 副本数 / 持有该任务的节点集。
        #: 队列空了但账非空 = 只剩尾巴 ⇒ 空闲槽按 pick_race_target 复制一份抢单。
        self.inflight: dict[tuple[int, int], int] = {}
        self.inflight_nodes: dict[tuple[int, int], set[str]] = {}
        self.node_games: dict[str, int] = {}
        #: 每任务的背压重排次数（跨 worker 共享，否则任务在节点间来回被推会无限重排）。
        self.busy_tries: dict[tuple[int, int], int] = {}
        #: 逐节点瞬断/硬失败计数：单元结束时入账，避免「熔断静默降本地」。
        self.node_soft_fails: dict[str, int] = {}
        self.node_hard_fails: dict[str, int] = {}
        self.jsonl_lock = threading.Lock()

        # ---- 通道机器（req 1/3/5）：就绪即派单；就绪不再 ping/传权重；失联重探 ----

    def lane_state(self, nd: dict) -> dict:
        """规范化节点描述 + 通道状态。

        worker / fetch_task 要的是 `{"id", "url", "key"}` 形态（旧实现由 alive 列表
        构造）；配置里的节点是 `{"id","url","authKey","concurrency"}` ⇒ 这里做一次
        归一，避免两套形状在各处硬取键（2026-09-19 实测：直接传配置 dict 会让
        fetch_task 取 `nd["key"]` 抛 KeyError，整批 0 局）。
        """
        nid = str(nd.get("id") or nd.get("url") or "?")
        lane = self.lanes.get(nid)
        if lane is None:
            lane = {
                "id": nid,
                "raw": nd,
                "nd": {
                    "id": nid,
                    "url": str(nd.get("url") or ""),
                    "key": str(nd.get("authKey") or ""),
                },
                "ready": False,
                "tripped": False,
                "next_try": 0.0,
                "tries": 0,
                "strikes": 0,
                "given_up": False,
                "c": max(1, int(nd.get("concurrency") or 1)),
            }
            self.lanes[nid] = lane
        return lane

    def bringup(self, lane: dict) -> bool:
        """未就绪节点的两步：ping → POST 权重；任一步失败 ⇒ recover_ping_sec 后重探。

        **就绪节点永不进来**（调用方只在 `not ready` 时调）——用户第 3 条：已经在正常
        工作的节点不要 ping、不要重传同一份权重，一直派活就好。
        """
        nd = lane["nd"]
        nid = str(lane["id"])
        lane["tries"] += 1
        with self.lock:
            self.node_pings[nid] = self.node_pings.get(nid, 0) + 1
        t0 = time.monotonic()
        try:
            ping = dist_common.node_ping(
                str(nd.get("url") or ""),
                str(nd.get("key") or ""),
                timeout=self.status_timeout,
            )
            ping_err = ""
        except Exception as e:  # node_ping 自吞异常；这里兜底防御
            ping = None
            ping_err = f': {str(e)[:80]}'
        dt = time.monotonic() - t0
        if ping is None:
            log(
                f'[batcheval] node {nid}: ping 失败/超时（{dt:.2f}s{ping_err}）'
                f' — {self.recover_ping_sec:.0f}s 后重探（就绪节点不会被重探）'
            )
            lane["next_try"] = time.time() + self.recover_ping_sec
            return False
        why = node_gate_reason(ping, self.local_bun, self.code_hash_local)
        if why:
            # B/C 严格：拒派不静默降级（P2 DoD；A 层门在 eval_dispatch，同一判据）。
            # 首次 + 之后每 3 次喊一次（20s 一次的重探不刷屏；节点升级后自动加入）。
            if lane["tries"] == 1 or lane["tries"] % 3 == 0:
                log(
                    f'[batcheval] node {nid}: {why} — 拒派（{self.recover_ping_sec:.0f}s 后重探；'
                    f'节点升级/修复后自动加入）'
                )
            lane["next_try"] = time.time() + self.recover_ping_sec
            return False
        t_w = time.monotonic()
        try:
            mode = dist_common.post_weights(
                str(nd.get("url") or ""),
                str(nd.get("key") or ""),
                self.iter_id,
                self.wver,
                self.weights_bytes,
                timeout=min(300.0, max(60.0, self.task_timeout)),
                kind=self.kind,
            )
        except Exception as e:
            log(
                f'[batcheval] node {nid}: weights POST 失败（{str(e)[:120]}）'
                f' — {self.recover_ping_sec:.0f}s 后重探'
            )
            lane["next_try"] = time.time() + self.recover_ping_sec
            return False
        dist_common.note_weights_pushed(self.wver, nid)
        lane["c"] = max(
            1, int(lane["raw"].get("concurrency") or ping.get("cpus") or 1)
        )
        lane["ready"] = True
        with self.lock:
            self.ready_lanes[0] += 1
            self.node_ready_at[nid] = time.time()
            self.nodes_ready_ever.add(nid)
        log(
            f'[batcheval] node {nid} 就绪（ping {dt:.2f}s + '
            f"weights {time.monotonic() - t_w:.2f}s, {mode}, {lane['c']} 槽）— 立即派单"
        )
        return True

    def mark_tripped(self, lane: dict) -> None:
        """节点掉线（连续真失败）：交出槽位并安排重探（用户第 5 条）。

        「重探」有界：连续 `max_recovery_tries` 轮**恢复后仍 0 局成功** ⇒ 认定节点是
        坏的（不是一时失联），本单元不再等它（否则一个必坏节点会拖满整窗）。
        """
        lane["ready"] = False
        lane["tripped"] = True
        lane["strikes"] = int(lane.get("strikes", 0)) + 1
        lane["next_try"] = time.time() + self.recover_ping_sec
        with self.lock:
            self.ready_lanes[0] = max(0, self.ready_lanes[0] - 1)
        if lane["strikes"] >= self.max_recovery_tries:
            lane["given_up"] = True
            log(
                f"[batcheval] node {lane['id']}: 连续 {lane['strikes']} 轮恢复后仍 0 局成功"
                f' — 本单元不再等它（其余节点/本机槽位继续；查节点日志与 /v1/ping）'
            )

    def spawn_workers(self, nd: dict, lane: dict, count: int) -> list[threading.Thread]:
        ws: list[threading.Thread] = []
        for i in range(count):
            t = threading.Thread(target=self.worker, args=(nd, lane, i), daemon=True)
            t.start()
            ws.append(t)
        return ws

    def supervise(self, nd: dict) -> None:
        """一个节点的通道：未就绪 → 重探；就绪 → 起槽位线程；掉线 → 交回重探。"""
        dist_common.set_request_tag(self.req_scope)
        lane = self.lane_state(nd)
        nid = str(lane["id"])
        while not self.all_done.is_set() and time.time() < self.deadline:
            if lane["given_up"]:
                return
            if not lane["ready"]:
                wait = lane["next_try"] - time.time()
                if wait > 0:
                    self.all_done.wait(min(1.0, wait))
                    continue
                if not self.bringup(lane):
                    continue
            lane["tripped"] = False
            ws = self.spawn_workers(lane["nd"], lane, lane["c"])
            for w in ws:
                w.join()
            if not lane["tripped"] or self.all_done.is_set():
                return
            self.mark_tripped(lane)
            log(
                f'[batcheval] node {nid} 掉线（连续真失败）— {self.recover_ping_sec:.0f}s 后重探，'
                f'ping 通即重新派单'
            )

    def window_open(self) -> bool:
        # 关窗即停派新局（§6.5 / 用户 2026-09-11：rollout 抢占让出集群）。
        # window_event 置位 = evalboard 可派；清位 = yield。None 退化为 deadline。
        if self.owner.window_event is None:
            return time.time() < self.deadline
        return self.owner.window_event.is_set()

    def record(self, manifest: dict, nd_id: str, task: tuple[int, int]) -> None:
        win = 1 if manifest.get("win") else 0
        cleared = 1 if manifest.get("cleared") else 0
        # x5⑧③：掉落三列与 A-eval record() 同源（eval_loot_fields）。
        loot = eval_loot_fields(manifest)
        # Phase 0 逐敌种画像七列与 A-eval 同源（eval_census_fields）。
        census = eval_census_fields(manifest)
        row = {
            "event": "eval",
            "iter": self.owner.batch.get("iter", 0),
            "wver": self.key16,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stage": task[0],
            "seed": task[1],
            "node": nd_id,
            "outcome": manifest.get("outcome"),
            "win": win,
            "cleared": cleared,
            "ticks": manifest.get("ticks"),
            "score": manifest.get("score"),
            "kills": manifest.get("kills"),
            "enemyHits": manifest.get("enemyHits"),
            "hitRate": manifest.get("hitRate"),
            "powerUpsCollected": manifest.get("powerUpsCollected"),
            "powerUpsSpawned": loot["powerUpsSpawned"],
            "starsCollected": loot["starsCollected"],
            "playerDamageTaken": manifest.get("playerDamageTaken"),
            "playerHits": manifest.get("playerHits"),
            "policy": manifest.get("policy", self.owner.policy),
            "enemyTotal": manifest.get("enemyTotal"),
            "playerDeaths": manifest.get("playerDeaths"),
            "playerShots": manifest.get("playerShots"),
            "playerLevel": manifest.get("playerLevel"),
            "cellsVisited": manifest.get("cellsVisited"),
            "firstKillTick": manifest.get("firstKillTick"),
            "stuckTicks": manifest.get("stuckTicks"),
            "puSpawnBomb": manifest.get("puSpawnBomb"),
            "puSpawnTank": manifest.get("puSpawnTank"),
            "puSpawnFreeze": manifest.get("puSpawnFreeze"),
            "puSpawnShield": manifest.get("puSpawnShield"),
            "puSpawnStar": manifest.get("puSpawnStar"),
            "puGotBomb": manifest.get("puGotBomb"),
            "puGotTank": manifest.get("puGotTank"),
            "puGotFreeze": manifest.get("puGotFreeze"),
            "puGotShield": manifest.get("puGotShield"),
            "puGotOther": loot["puGotOther"],
            "elapsedSec": manifest.get("elapsedSec"),
            "hitsByKind": census["hitsByKind"],
            "killsByKind": census["killsByKind"],
            "exposureByKind": census["exposureByKind"],
            "firstHitKind": census["firstHitKind"],
            "firstKillKind": census["firstKillKind"],
            "killOrder": census["killOrder"],
            "killerKinds": census["killerKinds"],
            # metrics v8 危险暴露四列（与 eval_row 同源，见 eval_v8_fields；
            # 缺键（旧节点/旧报告）= None，下游按缺省处理，不伪造）。
            **eval_v8_fields(manifest),
            # B 层归属（ingest → EvalStore 直读）
            "batch_id": self.owner.batch.get("batch_id"),
            "batch_unit": {"idx": self.owner.unit_idx, "of": self.owner.unit_of},
            "rung": self.unit["rung"],
            "ckpt_sha16": self.key16,
            "init_sha16": self.owner.init_sha16,
            "source": "B" if self.owner.policy == "nn" else "C",
        }
        # scoreV7 的原始输入（finalState + telemetry）原样带上——只有显式打开的
        # 调用方（eval_m1_once）会看到这一列；A/B/C 层的行不留任何新键。
        if self.owner.include_scorable:
            row["scorable"] = manifest.get("scorable")
        with self.jsonl_lock:
            with open(self.owner.eval_log, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(row) + "\n")
            _record_agent_meta(
                self.owner.eval_log.parent / "dist-agent-meta.jsonl",
                {
                    "node": nd_id,
                    "mode": "eval",
                    "it": self.owner.batch.get("iter", 0),
                    "stage": task[0],
                    "seed": task[1],
                    "ok": True,
                    "win": win,
                    "elapsedSec": manifest.get("elapsedSec"),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
        with self.lock:
            self.settled[0] += 1
            self.writers[0] = max(0, self.writers[0] - 1)
            self.node_games[nd_id] = self.node_games.get(nd_id, 0) + 1
            # 有进展 ⇒ 该节点可信，清零「恢复轮次」（坏节点 vs 一时失联的判据）。
            if nd_id in self.lanes:
                self.lanes[nd_id]["strikes"] = 0

    def params_for(self, stage_id: int) -> dict:
        """逐局参数（单 unit 跨多关）：`stageParams[str(stageId)]` 优先，缺省回落 unit 级值。

        旧行为（ladder/corpora 的单元）unit 级只有一个 stage ⇒ `stageParams` 缺省时
        逐字段回落，行为逐字不变。
        """
        p = self.stage_params.get(str(stage_id)) or self.stage_params.get(stage_id) or {}
        return {
            "maxTicks": int(p.get("maxTicks", self.unit["maxTicks"])),
            "difficulty": str(p.get("difficulty", self.unit.get("difficulty"))),
            "stageJson": str(p.get("stageJson", self.unit.get("stageJson"))),
            "lives": self.unit_lives if p.get("lives") is None else int(p["lives"]),
            "level": self.unit_level if p.get("level") is None else int(p["level"]),
        }

    def fetch_manifest(self, nd: dict, task: tuple[int, int]) -> dict:
        up = self.params_for(task[0])
        if nd["id"] == "local":
            assert self.local_weights is not None
            m = run_local_eval_game(
                self.owner.bun,
                self.local_weights,
                task[0],
                task[1],
                self.owner.eval_log.parent
                / "local-batcheval"
                / f'u{self.owner.unit_idx}_s{task[0]}_seed{task[1]}',
                max_ticks=up["maxTicks"],
                difficulty=up["difficulty"],
                timeout_sec=self.task_timeout,
                # 全量 wver：agent 按完整 sha 存桶；且本局 manifest.wver 就是它
                #（validate 用同一个值对账）。key16 只作行字段。
                wver=self.wver,
                stage_json=up["stageJson"],
                lives_override=up["lives"],
                player_level=up["level"],
                policy=self.owner.policy,
            )
        else:
            m, _files = dist_common.fetch_task(
                nd["url"],
                nd["key"],
                iter_id=self.iter_id,
                # 全量 wver（agent 桶按完整 sha 存；key16 会 409 —— 2026-09-19 实测）。
                wver=self.wver,
                stage=task[0],
                seed=task[1],
                max_ticks=up["maxTicks"],
                difficulty=up["difficulty"],
                timeout=self.task_timeout,
                mode="eval",
                kind=self.kind,
                stage_json=up["stageJson"],
                lives_override=up["lives"],
                player_level=up["level"],
                policy=self.owner.policy,
            )
        why = dist_common.validate_eval_result(m, self.wver)
        if why:
            raise dist_common.DistError(0, why)
        return m

    def worker(self, nd: dict, lane: dict, slot: int) -> None:
        dist_common.set_request_tag(self.req_scope)
        nid = str(nd["id"])
        while time.time() < self.deadline and not self.all_done.is_set():
            task = None
            race_copy = False
            with self.lock:
                if self.streaks.get(nid, 0) >= self.fail_streak_max:
                    # 熔断 → 交回通道：supervise 会每 recover_ping_sec 重探（req 5）。
                    if nid != "local":
                        lane["tripped"] = True
                    return
                # 关窗（rollout 抢占）本地与远端一并停派；在途局自然收完。
                if self.pending and self.window_open():
                    task = self.pending.popleft()
                    self.attempts[task] = self.attempts.get(task, 0) + 1
                    register_inflight(self.inflight, task)
                    self.inflight_nodes.setdefault(task, set()).add(nid)
                elif self.inflight:
                    # ---- 尾段竞速（与 A 层同机制）----
                    # 队列已空但还有在飞局 ⇒ 空闲槽复制一份到本节点，**先返回者结算**、
                    # 败者按 dup 丢弃。专项修的就是「快节点干完、只剩慢节点拖尾巴」：
                    # 实测 3 台慢节点（a96 平均 124s/局）只出 8.9% 的局却吃掉 63% 节点秒，
                    # 每单元尾巴空转 1–3 分钟（2026-09-19）。
                    cand = pick_race_target(self.inflight, nid, self.inflight_nodes, {})
                    if cand is not None and cand in self.seen:
                        # 不变式：inflight 里的任务必然 ∉ seen（赢家一律走 clear_inflight）。
                        # 残留 ⇒ 对它竞速只会「回包即 dup」，而 pop_inflight 在计数 > 0 时
                        # 会把本节点移出 inflight_nodes ⇒ 下一轮又能抢同一个 ⇒ 竞速风车
                        # （2026-09-19 实测：同一节点对同一已结算任务 5s 内重抢 48 次，
                        #  单批 411 条 tail-race + 111 条 dup settle，纯烧算力）。
                        # 摘除残留并放弃本次竞速（每轮至多清一个，有界）。
                        clear_inflight(self.inflight, self.inflight_nodes, cand)
                        cand = None
                    if cand is not None:
                        task = cand
                        self.inflight[task] += 1
                        self.inflight_nodes.setdefault(task, set()).add(nid)
                        # **不**递增 attempts：副本是投机重跑，若算进去，一局被 6 台
                        # 各抢一次后**一次真失败**就会直接耗满配额（局面被丢弃）。
                        # 该局必然已从 pending 领过一次 ⇒ 键已在（setdefault 兜底）。
                        self.attempts.setdefault(task, 1)
                        race_copy = True
            if task is None:
                if not self.pending and not self.inflight:
                    return
                if not self.window_open():
                    # yield：退出 worker，剩余 seed 留给下一次 idle 窗口续跑。
                    return
                self.all_done.wait(min(5.0, max(0.1, self.deadline - time.time())))
                continue
            attempt = self.attempts[task]
            if race_copy:
                log(
                    f'[batcheval] tail-race s{task[0]}/seed{task[1]} '
                    f'node={nid} — race lane'
                )
            sleep_for = 0.0
            manifest: dict | None = None
            t_task = time.monotonic()
            with self.lock:
                self.in_flight[0] += 1
            if self.trace:
                log(f'[batcheval] → {nid} s{task[0]}/seed{task[1]}')
            try:
                manifest = self.fetch_manifest(nd, task)
                ok = True
                err = ""
                transient = False
            except Exception as e:
                ok = False
                err = str(e)[:200]
                transient = is_transient_error(e)
            finally:
                with self.lock:
                    self.in_flight[0] -= 1
            # 竞速败者：结果与胜者逐字相同（同一 (stage,seed,wver) 纯函数），丢弃不落盘。
            dup_settle = False
            last_settled = False
            if ok:
                with self.lock:
                    if task in self.seen:
                        pop_inflight(self.inflight, self.inflight_nodes, task, nid)
                        dup_settle = True
                    else:
                        self.seen.add(task)
                        self.writers[0] += 1  # 收工前必须等它落盘（见 writers 注释）
                        clear_inflight(self.inflight, self.inflight_nodes, task)
                        self.streaks[nid] = 0
                        self.busy_tries.pop(task, None)
                        if len(self.seen) >= self.total:
                            self.all_done.set()
                            last_settled = True
            if dup_settle:
                self.dup_settles[0] += 1
                # 收工后的副本回包是**噪**（收尾汇总里给计数）；在飞期间的逐条留作
                # 竞速证据（且只在开了事件追踪时打，训练循环的日志逐字不变）。
                if self.trace and not self.all_done.is_set():
                    log(
                        f'[batcheval] dup settle s{task[0]}/seed{task[1]} node={nid} — dropped'
                    )
                continue
            if ok and manifest is not None:
                self.record(manifest, nd["id"], task)  # 只有胜者落盘（settled/node_games 同源）
                if self.trace:
                    # 事件：**单局结果返回**（含节点与耗时：与「→」配对可算在飞曲线）。
                    log(
                        f'[batcheval] ← {nid} s{task[0]}/seed{task[1]} '
                        f"{time.monotonic() - t_task:.1f}s ticks={manifest.get('ticks')} "
                        f"{manifest.get('outcome')}"
                    )
                if last_settled:
                    # 用户第 4 条：竞速后 settled 一满，**直接关闭所有节点的连接**——
                    # 立即！马上！right now！不等慢节点把尾巴算完（那些结果本就无用）。
                    closing = dist_common.abort_active_requests(self.req_scope)
                    log(
                        f'[batcheval] settled 满（{len(self.seen)}/{self.total}）— 断连 {closing} 条'
                        f'在飞连接 + 拒发新请求，立即收工（慢节点/竞速副本不再等）'
                    )
                continue
            with self.lock:
                pop_inflight(self.inflight, self.inflight_nodes, task, nid)
                # 竞速副本/收工断连的回包：本任务已被胜者结算（或本单元已收工）⇒ 结果
                # 无关，**丢弃且不计任何账**（既不是背压，也不是节点故障）。不加这一支，
                # 主动断连会被当成硬失败去熔断节点（2026-09-19）。
                if task in self.seen or self.all_done.is_set():
                    pass
                elif transient:
                    # 背压/瞬断（503 busy、10054 连接重置、超时）：**节点容量或网络**问题，
                    # 既不是任务问题、也不是节点故障 ⇒ 任务只重排、**永不丢弃**，
                    # 且不消耗 attempt 配额（退避指数封顶 BUSY_BACKOFF_CAP_SEC ⇒ 不烧 CPU；
                    # 真正的放弃由 deadline / all_done 兜底）。
                    # ⚠ 2026-09-19 修复：原条件是 `... and busy_tries < busy_retry_limit`，
                    # 上限用尽后掉进下面的「硬失败」分支 ⇒ attempts 被抬到 7（> 2）
                    # ⇒ 一局被误判「试满丢弃」⇒ settled 799/800 干等到 deadline。
                    n = self.busy_tries.get(task, 0) + 1
                    self.busy_tries[task] = n
                    self.pending.append(task)
                    # 回退 `pending.popleft()` 时累加的那一次（背压不是一次真尝试）。
                    self.attempts[task] = max(1, self.attempts.get(task, 1) - 1)
                    self.node_soft_fails[nid] = self.node_soft_fails.get(nid, 0) + 1
                    sleep_for = min(BUSY_BACKOFF_CAP_SEC, self.busy_backoff_sec * (2 ** (n - 1)))
                    if n == self.busy_retry_limit:
                        log(
                            f'[batcheval] ⚠ {nid} 对 s{task[0]}/seed{task[1]} 已连续 {n} 次背压'
                            f'（上限 {self.busy_retry_limit}）—— 任务继续排队，但该节点疑似持续'
                            f'满负荷（退避封顶 {BUSY_BACKOFF_CAP_SEC:.0f}s，不再计入失败）'
                        )
                    log(
                        f'[batcheval] {nid} s{task[0]}/seed{task[1]} 背压（{err[:90]}）'
                        f'→ 退避 {sleep_for:.2f}s 重排（第 {n} 次；不计节点失败）'
                    )
                else:
                    self.streaks[nid] = self.streaks.get(nid, 0) + 1
                    self.node_hard_fails[nid] = self.node_hard_fails.get(nid, 0) + 1
                    if attempt < EVAL_TASK_ATTEMPTS and task not in self.seen:
                        self.pending.append(task)
                        log(
                            f'[batcheval] {nid} s{task[0]}/seed{task[1]} failed ({err}) — '
                            f'requeued（第 {attempt}/{EVAL_TASK_ATTEMPTS} 次）'
                        )
                    else:
                        # 试满配额（或已结算）⇒ 不再重排，但**失败本身必须留痕**：
                        # 2026-09-19 实测「真失败计数 gcs=1」却在日志里查不到任何原因
                        #（原实现只有「会重排」那条才打日志）——静默计数正是排查黑洞。
                        log(
                            f'[batcheval] {nid} s{task[0]}/seed{task[1]} failed ({err}) — '
                            f'试满 {attempt}/{EVAL_TASK_ATTEMPTS} 次，本单元不再重排'
                        )
                    if self.streaks[nid] == self.fail_streak_max:
                        if nid != "local":
                            lane["tripped"] = True  # supervise 收工后会重探（req 5）
                        log(
                            f'[batcheval] ⚠ 节点 {nid} 连续 {self.fail_streak_max} 次真失败'
                            f' → 停派该节点，{self.recover_ping_sec:.0f}s 后重探'
                            f'（其余节点继续；/v1/ping 看 codeHash 与节点日志）'
                        )
            if sleep_for > 0:
                # 锁外退避（持锁睡会卡住全部 worker）。
                time.sleep(sleep_for)

    def _watch(self) -> None:
        while not self.stop_watch.wait(2.0):
            with self.lock:
                p, inf, st = len(self.pending), self.in_flight[0], self.settled[0]
            log(
                f'[batcheval] ⏱ u{self.owner.unit_idx} pending={p} inflight={inf} '
                f'settled={st}/{self.total}'
            )

    def run(self) -> dict:
        threads: list[threading.Thread] = []
        # 本机槽位：无门无权重 ⇒ **立刻**开工，不等任何节点（req 1 的精神）。
        if self.local_on:
            local_lane = {
                "id": "local",
                "nd": {"id": "local"},
                "ready": True,
                "tripped": False,
                "next_try": 0.0,
                "tries": 0,
                "c": self.local_slots,
            }
            threads += self.spawn_workers({"id": "local"}, local_lane, self.local_slots)
        # 每节点一条通道：各自就绪即派单；失联每 recover_ping_sec 重探（req 1/3/5）。
        for nd in self.enabled_nodes:
            t = threading.Thread(target=self.supervise, args=(nd,), daemon=True)
            t.start()
            threads.append(t)
        if self.trace:
            # 每 2s 一行在飞采样：掉档时一眼看出是「队列空了」还是「派发停了」。

            threading.Thread(target=self._watch, daemon=True).start()
        # 收尾等待：settled 一满（all_done 由最后结算的 worker 置位）或墙钟到点即收工。
        # 「零消费者」只在**整批从未有过任何消费者**时生效（无节点就绪过 + 本机槽位 0）
        # ⇒ 有界响亮收摊，而不是默认 86400s 窗口里干等。
        stuck_since: float | None = None
        while not self.all_done.is_set() and time.time() < self.deadline:
            # 僵死兜底（2026-09-19）：某局在重试配额耗尽后被丢弃（见上文 failed 行）时
            # `len(seen) >= total` 永不成立，上面两个 break 条件也不会命中 ⇒ 原先只能干等到
            # deadline（实测 settled=799/800、pending=0、inflight=0 空转两分钟，用户手动停）。
            # 队列空 + 无在飞 + 计数不再变化持续 STUCK_GRACE_SEC ⇒ 判定缺局，响亮收工。
            with self.lock:
                _idle = (not self.pending) and self.in_flight[0] == 0 and len(self.seen) < self.total
                _settled_n = len(self.seen)
            if _idle:
                if stuck_since is None:
                    stuck_since = time.time()
                elif time.time() - stuck_since > STUCK_GRACE_SEC:
                    log(
                        f"[batcheval] ⚠ {self.unit['rung']} u{self.owner.unit_idx}: 收尾僵死 — "
                        f'settled={_settled_n}/{self.total}，队列空且无在飞；'
                        f'{self.total - _settled_n} 局在重试配额耗尽后被丢弃（见上文 failed 行）'
                        f'⇒ 立即收工，不再空等'
                    )
                    break
            else:
                stuck_since = None
            if (
                not self.local_on
                and self.enabled_nodes
                and self.ready_lanes[0] <= 0
                and all(lz["given_up"] for k, lz in self.lanes.items() if k != "local")
            ):
                log(
                    f"[batcheval] {self.unit['rung']} u{self.owner.unit_idx}: 全部节点通道已放弃"
                    f'（连续 {self.max_recovery_tries} 轮恢复后仍无进展）且本机槽位 0 — 收摊'
                )
                break
            if (
                not self.local_on
                and not self.nodes_ready_ever
                and time.time() - self.t_start > self.no_consumer_grace
            ):
                log(
                    f"[batcheval] {self.unit['rung']} u{self.owner.unit_idx}: {self.no_consumer_grace:.0f}s 内"
                    f' 无任何节点就绪且本机槽位 0 — 本批无人可跑（deferred；查 /v1/ping、'
                    f'codeHash 与 rl-config 的节点/本机槽位）'
                )
                break
            self.all_done.wait(0.2)
        return self.owner._settle_unit(
            lock=self.lock,
            seen=self.seen,
            nodes_ready_ever=self.nodes_ready_ever,
            writers=self.writers,
            dup_settles=self.dup_settles,
            stop_watch=self.stop_watch,
            all_done=self.all_done,
            node_games=self.node_games,
            node_soft_fails=self.node_soft_fails,
            node_hard_fails=self.node_hard_fails,
            threads=threads,
            unit=self.unit,
            req_scope=self.req_scope,
            todo=self.todo,
            t_start=self.t_start,
        )


def dispatch_batch_bg(
    bun: str,
    rl_path: str | None,
    eval_log: Path,
    args,
    cfg: dict,
    batch: dict,
    unit: dict,
    unit_idx: int,
    unit_of: int,
    run_id: str,
    engine_epoch: str,
    policy: str = "nn",
    window_event: threading.Event | None = None,
    init_sha16: str = "",
) -> threading.Thread:
    """后台起一个单元（调用方 join，语义同 dispatch_eval_bg）。"""
    runner = BatchEvalRunner(
        bun,
        rl_path,
        eval_log,
        args,
        cfg,
        batch,
        unit,
        unit_idx,
        unit_of,
        run_id,
        engine_epoch,
        policy,
        window_event,
        init_sha16,
    )
    t_ = threading.Thread(target=runner.run, daemon=True, name=f"batcheval-u{unit_idx}")
    t_.start()
    return t_


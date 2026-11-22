"""batch_runner — B 层单元执行器（`BatchEvalRunner` / `dispatch_batch_bg`）。

2026-09-25（S27/B3）从 `trainer/batch_eval.py` **纯搬**出来（成员逐字节对账见
`tests/trainer/test_batch_runner_split.py`）—— 那是 §5.5.4 的第三步：B1 把纯规划/判据送到
`biz/batch_plan.py`、B2 把台账收进 `trainer/batch_store.py`，本模块收走**执行面**。
一个单元 = 一个 100 局批次：通道机器（每节点各自 ping+权重就绪即派单）· 尾段竞速 ·
背压重排 · 收尾三闸（settled / 僵死 / 零消费者宽限）。

**这个模块的边**：`common.*`（distribution / codehash / codehash_ref / task_client /
weights_client / runtime / log / proc）· `biz.batch_plan` · `remote.weights_standard` ·
`worker.eval_yield` · `trainer.{batch_lanes,batch_store}`；**不** import 旧家 `trainer.batch_eval`
（否则成环）；`trainer.batch_eval` 反过来再导出本模块的公开名。

**★ 注入点（patch 目标）**：依赖注入靠**模块全局**，测试 `monkeypatch.setattr` 打的是**读它的
那个模块**。2026-11-20 通道机器整块搬去 `trainer/batch_lanes.py` 后，锚点分两家：
  * 本模块（执行器那一侧）：`log`（单元开始/收尾行）· `TOOLCHAIN`（节点版本门的本机值，
    经 `common.proc.Toolchain` 端口）·
    `BatchStore`（台账写点）· `RECOVER_PING_SEC` / `NO_CONSUMER_GRACE_SEC` / `NODE_RECOVERY_TRIES`。
  * `trainer.batch_lanes`（机器那一侧）：`log` · `_record_agent_meta` · `run_local_eval_game` ·
    `BUSY_BACKOFF_CAP_SEC` / `STUCK_GRACE_SEC`。
  打在错的家 = **静默空操作**（名字还在、没人读）⇒ `tests/trainer/test_batch_runner_split.py`
  按「谁裸名读、谁必须绑定」逐名判，两边都算。

**关键契约**（原文照搬，别在本模块里改语义）：
  - 节点门（§6.6）：enabled ∧ ping ∧ evalSupport ∧ stageJsonSupport ∧ bunVersion 一致 ∧
    **codeHash 一致**（= rollout 门同一判据）——**严格拒派，不静默降级**（P2 DoD）。
  - iterId 命名空间 `{runId}.b{batchShort}u{unit}`：agent taskKey 无 policy 分量，
    命名空间隔离是 god/nn 不串键的唯一保证。
  - 窗口（§6.5）：只在窗口开时派新局；在途局自然跑完（taskTimeoutSec 封顶）；
    剩余局按 batch_unit 下窗续跑（`units.done` 落 `batches.jsonl` 台账，见 `trainer/batch_store.py`）。
  - 确定性契约（§3.4/P2 DoD）：同 ckpt + 同 seed 集 + 同 stage ⇒ gameplay 字段逐字节一致
    （node/elapsedSec/phase/ts 不要求）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import common.codehash
import common.codehash_ref as codehash_ref
import common.distribution
import common.runtime
import common.task_client
import common.weights_client
from biz.batch_plan import batch_iter_id, kind_for_policy
from common.log import default_router, log
from common.proc import TOOLCHAIN
from remote import weights_standard
from trainer.batch_lanes import UnitLedger, _UnitLanes, _UnitPlan
from trainer.batch_store import BatchStore, data_root
from worker.eval_yield import EVAL_LOCAL_SLOTS_DEFAULT

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
    """R4-G1：写 EvalBoard 心跳（心跳绝不打断单元，但失败**不复述成静默**）。

    `write_state` 自己会把落盘失败喊出来（`worker/eval_heartbeat.py`），这里兜的是它**之前**
    的失败：`worker.eval_heartbeat` 导不进来（打包/路径）、以及它内部任何别的异常。这两种在
    console 上的表现与「心跳根本没调」长得一模一样 —— 「批评估忙了一小时」和「心跳早就不写了」
    都只是屏幕上的一行旧状态，而唯一会去看的人正是靠它决定要不要干预。
    """
    try:
        from worker.eval_heartbeat import write_state

        write_state(**patch)
    except Exception as e:  # 契约：降级，绝不反杀单元
        default_router().warn_once(
            "eval_heartbeat:batch",
            f"批评估心跳没写成：{e!r}——console 的 runner_state 会停在旧值",
        )


@dataclass(frozen=True, kw_only=True)
class RunnerSpec:
    """一个单元的全部输入（`BatchEvalRunner` 与 `dispatch_batch_bg` 共用一张字段表）。

    ## 为什么要有它

    这两处原来是**同一张 13 参表的两份**：`dispatch_batch_bg` 按**位置**转手给
    `BatchEvalRunner(...)`。相邻的 `unit_idx` / `unit_of` 都是 `int`、`init_sha16` 与
    `run_id` 都是 `str` —— 传错一位不报错：`unit_of` 进了 `unit_idx` 只会让 iterId 的
    命名空间变样（`{runId}.b{batchShort}u{unit}` 是 god/nn 不串键的唯一保证），要等到台账
    里出现「本单元之外」的键才发现。关键字拼错同理：静默落回缺省（`policy="nn"` 落错 =
    节点门按错 kind 判，`include_scorable=True` 落丢 = A/B/C 行悄悄少列）。

    ## 字段分组

    前 11 个是身份/上下文（缺一个就不知道在跑谁），后 5 个是带缺省的旋钮（`kind` 缺省由
    policy 推，见 `KIND_FOR_POLICY`）。`args` 只被 `getattr(args, "eval_window_sec", …)`
    读过 ⇒ 保持不透明句柄 `object`（不为它造协议）。
    """

    bun: str
    rl_path: str | None
    eval_log: Path
    args: object
    cfg: dict
    batch: dict
    unit: dict
    unit_idx: int
    unit_of: int
    run_id: str
    engine_epoch: str
    #: `nn` / `god`；决定节点门与 kind 推法。
    policy: str = "nn"
    #: 开窗事件；None = 一直开（一次性评估）。
    window_event: threading.Event | None = None
    #: 权重指纹（逐局行的 `wver`）。
    init_sha16: str = ""
    #: None = 由 policy 推（`kind_for_policy`）；显式给值只为一处例外（一次性评估复用桶名）。
    kind: str | None = None
    #: 把 agent 的原始 `scorable` 原样落到逐局行（缺省关 ⇒ A/B/C 层行逐字节不变）。
    include_scorable: bool = False


class BatchEvalRunner:
    """一批次中一个 100 局单元的派发器（阻塞版，调用方放后台线程跑）。

    任何失败只记日志，绝不抛出。窗口关闭（window_event 未置位且超时）即停派
    新局，在途局自然收完；剩余 seed 下窗续跑（units.done 已落台账）。
    """

    def __init__(self, spec: RunnerSpec) -> None:
        bun, rl_path, eval_log = spec.bun, spec.rl_path, spec.eval_log
        args, cfg, batch, unit = spec.args, spec.cfg, spec.batch, spec.unit
        unit_idx, unit_of = spec.unit_idx, spec.unit_of
        run_id, engine_epoch, policy = spec.run_id, spec.engine_epoch, spec.policy
        window_event, init_sha16 = spec.window_event, spec.init_sha16
        kind, include_scorable = spec.kind, spec.include_scorable
        self.bun = bun
        self.rl_path = rl_path
        self.eval_log = eval_log
        self.args = args
        self.cfg = cfg
        self.batch = batch
        self.unit = unit
        self.unit_idx = unit_idx
        self.unit_of = unit_of
        #: 本单元的所有线程（节点槽位 / 监管 / 每 2s 采样）归这里 —— 一端一份，名字带单元号，
        #: 转储里能直接对上是哪一批；谁 join 仍由调用方的 `threads` 表决定。
        self._rt = common.runtime.Runtime(scope=f"batcheval-u{unit_idx}")
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

        args, unit = self.args, self.unit
        policy_cfg = self.cfg.get("policy", {})
        status_timeout = common.distribution.status_timeout_of(policy_cfg)
        task_timeout = float(policy_cfg.get("taskTimeoutSec", 900))
        fail_streak_max = int(policy_cfg.get("nodeFailStreak", 3))
        # 背压参数（与 biz/bc_dispatch 的 busy 重排同源，默认值取同一量级）：
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
            # ★ 2026-10-15：指纹与推给节点的字节都必须是**整份** —— 离线腿回传的 delta 容器
            #   （hub 原样落位）会让本机指纹与节点 cache 键对不上，且节点侧 `JSON.parse` 直接
            #   报 `BWD1`。`read_standard` = 读侧落地 + 读字节（幂等；整份只读 4 字节）——
            #   两件事都落在**读盘之后**，所以指纹必须算在它后面。
            weights_bytes = weights_standard.read_standard(self.rl_path)
            wver = common.distribution.weights_fingerprint(self.rl_path)
            key16 = wver[:16]
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
        # 只清自己的作用域：全局清空会把兄弟课程刚落下的收工态重新解封。
        common.distribution.clear_abort(req_scope)
        # 单 unit 跨多关（一次性评估：用户 2026-09-19 第 2 条「不要 u0/u1/u2 阶段」）：
        # unit["pairs"] = [[stageId, seed], …] 时以它为准；缺省仍由 seeds × stageId 展开
        #（ladder/corpora 的单元逐字不变）。逐关参数走 unit["stageParams"]，见 params_for。
        if unit.get("pairs"):
            pairs = [(int(p[0]), int(p[1])) for p in unit["pairs"]]
        else:
            seeds = [int(s) for s in unit["seeds"]]
            pairs = [(int(unit["stageId"]), s) for s in seeds]
        total, done_before = len(pairs), self._done_keys(key16)
        todo = [p for p in pairs if p not in done_before]
        if not todo:
            log(f"[batcheval] {unit['rung']} u{self.unit_idx}: already settled — skip")
            return {"settled": total, "total": total, "dropped": 0}
        t_start, trace = time.time(), common.distribution.trace_enabled()
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

        local_bun = TOOLCHAIN.bun_version(self.bun)
        # 节点门指纹（2026-09-17 统一）：与 rollout 门同源 = SSOT 清单的 codeHash。
        # ---- 派发通道：**每节点各自就绪、各自派单**（用户 2026-09-19 五条裁定）----
        # 旧实现是三个**串行阶段**：ping 全部节点 → 全部节点收权重 → 才开派。慢节点把整批
        # 拖住（实测 gate 6.83s + weights 0.79s，**且每个单元重跑一遍**），而**已经就绪的
        # 节点在这段时间里空转**（用户：「一个节点权重分发成功后立即派发任务」）。现在每个
        # 节点一条通道：ping 通 → 传权重 → **立刻**派活，不等任何别的节点；就绪的节点不再
        # 被 ping、不再重传同一份权重；失联节点每 recover_ping_sec 重探，通了立即派单。
        enabled_nodes = [n for n in self.cfg.get("nodes", []) if n.get("enabled", True)]
        # 节点门指纹（2026-09-17 统一；2026-11-15 起参考值 = self 自报）：与 rollout 同源。
        code_hash_local = codehash_ref.reference_code_hash(enabled_nodes, timeout=status_timeout)
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

        机器本体（状态 + 方法 + 主循环）住 `trainer/batch_lanes.py::_UnitLanes`；本方法只剩
        「构造 → 跑」。它的依赖注入锚点随之搬到那个模块（`trainer.batch_lanes.*`）。
        """
        return _UnitLanes(self, plan).run()

    def _settle_unit(self, ledger: UnitLedger) -> dict:
        """收尾三闸：停机 → 断连 → 只等**正在写行的赢家**落盘 → 线程 best-effort → 落账。

        这里是「一次批单元」对台账的唯一交代：`dropped == 0` ⇒ `mark_unit_done`（含
        `node_dist` 参与度）；否则 `reopen_for_resume`（部分完成，下窗续跑）。两者都在
        `try` 里 —— B 层的规矩是**任何失败只记日志、绝不抛出**（训练主链零风险）。

        入参是机器交出来的一整本账（`UnitLedger`，曾摊成 15 个关键字参数）：下面把名字
        逐个读出来，方法体与收口顺序逐字未动。
        """
        lock, seen = ledger.lock, ledger.seen
        writers, dup_settles = ledger.writers, ledger.dup_settles
        stop_watch, all_done = ledger.stop_watch, ledger.all_done
        node_games, node_soft_fails = ledger.node_games, ledger.node_soft_fails
        node_hard_fails = ledger.node_hard_fails
        nodes_ready_ever, threads = ledger.nodes_ready_ever, ledger.threads
        unit, req_scope = ledger.unit, ledger.req_scope
        todo, t_start = ledger.todo, ledger.t_start

        stop_watch.set()
        # req 4：收工即断连（settled 满或墙钟到点都一样——在途局结果无用，不许再拖着等）。
        closing = common.distribution.abort_active_requests(req_scope)
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


def dispatch_batch_bg(spec: RunnerSpec) -> threading.Thread:
    """后台起一个单元（调用方 join，语义同 dispatch_eval_bg）。

    与 `BatchEvalRunner` 共用同一份 `RunnerSpec` ⇒ 这里不再有「按位置转手 13 个参数」的
    那一层（原写法传错一位只会在台账里显形）。
    """
    runner = BatchEvalRunner(spec)
    return runner._rt.thread(runner.run, name="run", daemon=True)


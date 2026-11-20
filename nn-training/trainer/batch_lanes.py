"""batch_lanes — B 层单元的**通道机器**（`_UnitLanes`）、它的契约与交割对象。

2026-11-20：从 `trainer/batch_runner.py` 整块搬出 —— 原模块 LOC 顶在 999/1000，`UnitLedger`
（收尾要的 15 个把手）没有落脚空间。搬的是**机器本体**：`_UnitPlan`（单元前置条件，
`_open_unit` 的产出）· `_UnitLanes`（每节点一条通道的状态 + 方法 + 主循环）· 两个只被机器读的
常量。

**边**：单向 —— `trainer/batch_runner.py` import 本模块，本模块**从不**回头 import 它。机器认识
执行器只有一个面：`self.owner`（用于 `unit_idx` / `eval_log` / `_rt` / `_settle_unit`）。

**★ 注入点（patch 目标跟着读它的模块走）**：依赖注入靠模块全局，`monkeypatch.setattr` 必须打在
**读它的那个模块**上：
  * 本模块：`log`（通道日志）· `_record_agent_meta`（机器健康行）· `run_local_eval_game`
    （本机槽位）· `STUCK_GRACE_SEC` / `BUSY_BACKOFF_CAP_SEC`（机器独占常量）。
  * 仍住 `trainer.batch_runner`：`bun_version` / `RECOVER_PING_SEC` / `NO_CONSUMER_GRACE_SEC` /
    `NODE_RECOVERY_TRIES` / `BatchStore`（执行器那一侧）。
  打在错的家 = **静默空操作**（名字还在、没人读）⇒ `tests/trainer/test_batch_runner_split.py`
  按「谁裸名读、谁必须绑定」逐名判。

**台账边界**：机器不认识台账（零 `store.` / 零 `mark_unit_done` / `reopen_for_resume`）——
对台账的唯一交代仍在 `BatchEvalRunner._settle_unit`，它收本模块的 `UnitLedger`（一次交清）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

import common.distribution
import common.runtime
import common.task_client
import common.weights_client
from biz.batch_plan import (
    eval_rule_kwargs,
    eval_rules_reject_reason,
    is_transient_error,
    node_gate_reason,
    node_supports_decision_k,
)
from common.fs import append_jsonl
from common.log import log
from trainer.queue_local import (
    clear_inflight,
    pick_race_target,
    pop_inflight,
    register_inflight,
)
from worker import node_identity
from worker.agent_meta import record_agent_meta as _record_agent_meta
from worker.eval_local import (
    EVAL_TASK_ATTEMPTS,
    eval_census_fields,
    eval_loot_fields,
    eval_metric_fields,
    run_local_eval_game,
)


@dataclass(frozen=True, kw_only=True)
class UnitLedger:
    """一次批单元的**台账把手**（`_settle_unit` 曾是 15 个关键字参数）。

    这些是**活的共享对象**（锁 / 计数 / 事件 / 线程），不是值：`frozen` 只保证没人把字段
    换成另一个对象，计数器本身照旧由机器与 worker 就地改写 —— 收尾读的就是同一份账。

    `threads` 单独一个参数进 `ledger()`：它是本次 `run()` 起的线程表，不属于状态块。
    """

    lock: threading.Lock
    seen: set[tuple[int, int]]
    nodes_ready_ever: set[str]
    writers: list[int]
    dup_settles: list[int]
    stop_watch: threading.Event
    all_done: threading.Event
    node_games: dict[str, int]
    node_soft_fails: dict[str, int]
    node_hard_fails: dict[str, int]
    threads: list[threading.Thread]
    unit: dict
    req_scope: str
    todo: list[tuple[int, int]]
    t_start: float


#: 背压退避封顶（秒）。指数序列 0.25/0.5/1/2/4/8 覆盖 6 次重排。
BUSY_BACKOFF_CAP_SEC = 8.0
#: 收尾僵死兜底（2026-09-19）：队列空 + 无在飞 + settled 不再变化持续这么久 ⇒ 认定有局在
#: 重试配额耗尽后被丢弃（收尾条件 `len(seen) >= total` 永不成立），立即响亮收工，
#: 而不是干等到 deadline。取 10s：足够容纳回包 / 背压重排 / 节点重探的间隙。
STUCK_GRACE_SEC = 10.0

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

class LaneOwner(Protocol):
    """机器认识的**执行器面**（唯一耦合）：11 个属性 + 收尾入口。

    结构类型，不继承 —— `BatchEvalRunner` 不必知道这个协议存在，本模块也不必 import 它
    （反向边 = 环）。新增耦合必须在这里显式加一行，否则 mypy 会拒。
    """

    unit_idx: int
    unit_of: int
    policy: str
    bun: str
    eval_log: Path
    batch: dict
    window_event: threading.Event | None
    init_sha16: str
    include_scorable: bool
    _rt: common.runtime.Runtime

    def _settle_unit(self, ledger: UnitLedger) -> dict: ...


class _UnitLanes:
    """一个单元的**通道机器**（原 `_run_channels` 体内：状态 + 11 个闭包 + 起跑 + 主循环）。

    `self.*` = 本次单元运行的状态；执行器在 `self.owner`（面见 `LaneOwner`）。依赖注入
    （`log` / `_record_agent_meta` / `run_local_eval_game` / `STUCK_GRACE_SEC`）按**本模块**
    全局解析 —— 2026-11-20 起类住本模块，所以 patch 锚点就是 `trainer.batch_lanes.*`
    （打在旧家 `trainer.batch_runner.log` 是静默空操作，守卫在 `test_batch_runner_split`）。
    """

    def __init__(self, owner: LaneOwner, plan: _UnitPlan) -> None:
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
            ping = common.distribution.node_ping(
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
        # eval 规则 / 事件门 / hybrid opening：统一能力位门（旧 agent 拒派，不静默降级）。
        why_rules = eval_rules_reject_reason(getattr(self.owner, "args", None), ping)
        if why_rules:
            if lane["tries"] == 1 or lane["tries"] % 3 == 0:
                log(f'[batcheval] node {nid}: {why_rules}')
            lane["next_try"] = time.time() + self.recover_ping_sec
            return False
        # 决策周期 K（plan/k5-rhythm.plan.md）：本批 K≠10 ⇒ 无能力位节点拒派
        # （旧 agent 静默跑 K=10 混入；与 decisionEventsSupport 同规；缺省 10 = 不查）。
        if int(getattr(getattr(self.owner, "args", None), "decision_k", 10)) != 10 and not node_supports_decision_k(ping):
            if lane["tries"] == 1 or lane["tries"] % 3 == 0:
                log(
                    f'[batcheval] node {nid}: 缺 decisionKSupport 能力位（旧 agent）——'
                    f'K≠10 任务被拒；不降级'
                )
            lane["next_try"] = time.time() + self.recover_ping_sec
            return False
        # 顶钢空枪门/拆砖：开启批只派给有能力位的节点（旧 agent 静默跑关闭态 = 混批）。
        if sso_why := eval_rules_reject_reason(getattr(self.owner, "args", None), ping):
            if lane["tries"] == 1 or lane["tries"] % 3 == 0:
                log(f'[batcheval] node {nid}: {sso_why}')
            lane["next_try"] = time.time() + self.recover_ping_sec
            return False
        # 本轮（本单元）bootId 核（plan/sampler-single-instance.plan.md §8-Q2）：就绪节点永不
        # 进本函数 ⇒ 这条只在「掉线重探 / 中途上线」上生效——回场时发现端口上换过进程，
        # 本单元拒派。复用 `given_up`（= 本单元不再等它）：主循环的「全部通道已放弃 ⇒ 收摊」
        # 与「本机槽位 0」的退出条件因此照旧成立，不会把单元拖满整窗。
        boot_why = node_identity.note_ping(
            node_identity.round_key("batch", self.iter_id), nid, ping
        )
        if boot_why:
            lane["given_up"] = True
            log(f"[batcheval] node {nid}: {boot_why} — 本单元拒派（不再重探）")
            _record_agent_meta(
                self.owner.eval_log.parent / "dist-agent-meta.jsonl",
                {
                    "node": nid,
                    "mode": "eval",
                    "it": self.owner.batch.get("iter", 0),
                    "ok": False,
                    "reason": boot_why,
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            return False
        t_w = time.monotonic()
        try:
            mode = common.weights_client.post_weights(
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
        common.distribution.note_weights_pushed(self.wver, nid)
        # 与 rollout/eval 腿同一份口径（2026-11-14）：config 优先，缺省 workers → cpus。
        lane["c"] = common.distribution.node_slots(
            lane["raw"].get("concurrency"), ping.get("cpus"), ping.get("workers")
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

    def _spawn(self, target, name: str, *args: object) -> threading.Thread:
        """起一个**具名** daemon 线程（归本单元的 `Runtime`：名字 `batcheval-u<idx>-<name>`）。

        没名字的线程在 `faulthandler` / `py-spy` 里只显示 `Thread-N`，出事时现场分不清它是
        在等本机队列、监管某个节点，还是采样线程。
        """
        return self.owner._rt.thread(target, *args, name=name, daemon=True)

    def spawn_workers(self, nd: dict, lane: dict, count: int) -> list[threading.Thread]:
        ws: list[threading.Thread] = []
        for i in range(count):
            ws.append(self._spawn(self.worker, f"worker:{nd.get('id')}-{i}", nd, lane, i))
        return ws

    def supervise(self, nd: dict) -> None:
        """一个节点的通道：未就绪 → 重探；就绪 → 起槽位线程；掉线 → 交回重探。"""
        # `kind="batch"`（2026-11-14）：节点腿自成 `batch` 计数桶（与同进程 rollout/eval 分开）。
        common.distribution.set_request_tag(self.req_scope, kind="batch")
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
            # metrics v8–v13 全族（与 `eval_rows.eval_row` 同源，见 `eval_metric_fields`；
            # 缺键（旧节点/旧报告）= 整键省略，下游按缺省处理，不伪造）。
            # ⚠ 只能走聚合入口，不许逐族展开：本文件已顶到
            # `tests/test_python_loc_budget.py` 的 1000 LOC 天花板，逐族展开会让
            # 「加一族 = 加一行」的账压在这里（v13 就是这么顶爆的）。
            **eval_metric_fields(manifest),
            # 新纪元死刑通道（plan §2 #9/P1-2 方案 a）：裸透传；旧报告缺键 = None，
            # 读数方按无信号处理（与 EvalCourseRow 可选字段同约）。
            "moveHist": manifest.get("moveHist"),
            "decisions": manifest.get("decisions"),
            "idleTicks": manifest.get("idleTicks"),
            "stopRuns": manifest.get("stopRuns"),
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
        # 每路径单写者（与 A 层同一条 `eval_log.jsonl` / `dist-agent-meta.jsonl`）：
        # B/C 行与重叠的 A 层评估轮也不会互相覆盖行首。
        append_jsonl(self.owner.eval_log, row)
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
                # R2 事件 rung：手动 judge 由 spec.decisionEvents 驱动（缺席 = 老行为）。
                decision_events=bool(getattr(getattr(self.owner, "args", None), "decision_events", False)),
                # 决策周期 K：手动 judge 由 spec.decisionK 驱动（缺席/10 = 老行为）。
                decision_k=int(getattr(getattr(self.owner, "args", None), "decision_k", 10)),
                # 顶钢空枪门/拆砖（课程 inference_rules；缺席 = 老行为）。
                **eval_rule_kwargs(getattr(self.owner, "args", None)),
            )
        else:
            m, _files = common.task_client.fetch_task(common.task_client.FetchTaskSpec(
                    url=nd["url"],
                    auth_key=nd["key"],
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
                    # R2 事件 rung / 决策周期 K / 推理规则：与本机份额同源（缺席 = 老行为）。
                    decision_events=bool(getattr(getattr(self.owner, "args", None), "decision_events", False)),
                    decision_k=int(getattr(getattr(self.owner, "args", None), "decision_k", 10)),
                    **eval_rule_kwargs(getattr(self.owner, "args", None)),
                ))
        why = common.task_client.validate_eval_result(m, self.wver)
        if why:
            raise common.distribution.DistError(0, why)
        return m

    def worker(self, nd: dict, lane: dict, slot: int) -> None:
        common.distribution.set_request_tag(self.req_scope, kind="batch")
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
                ok, err, transient = True, "", False
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
                    closing = common.distribution.abort_active_requests(self.req_scope)
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

    def ledger(self, *, threads: list[threading.Thread]) -> UnitLedger:
        """把本单元的 15 个把手一次交清（`threads` 是 `run()` 刚起的线程，不在状态块里）。

        这是 `UnitLedger` 的**唯一**装配点：字段就住在本类的状态块，换名字编译器会指到这行。
        """
        return UnitLedger(
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
            threads.append(self._spawn(self.supervise, f"supervise:{nd.get('id')}", nd))
        if self.trace:
            # 每 2s 一行在飞采样：掉档时一眼看出是「队列空了」还是「派发停了」。

            self._spawn(self._watch, "watch")
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
        return self.owner._settle_unit(self.ledger(threads=threads))

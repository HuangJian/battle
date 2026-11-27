"""dispatch 的相位状态与节点准入表（从 `trainer/dispatch.py` 拆出，LOC 预算）。"""

from __future__ import annotations

import argparse
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import common.distribution
import remote.node_upgrade
from common import node_report
from common.log import log
from common.proc import version_mm as mm
from worker import node_identity
from worker.agent_meta import record_agent_meta as _record_agent_meta


@dataclass(frozen=True, kw_only=True)
class DispatchSpec:
    """`run_rollout_queue` / `RolloutDispatcher` 的旋钮袋（P0-3：20 参 → 一个对象）。

    两个入口（薄包装与 OO 本体）**共用同一张字段表** —— 旧版 `run_rollout_queue` 把 19 个
    参数按位置转手给 `RolloutDispatcher.__init__`（只差一个 `on_shard` 靠关键字），传错
    一位不会报错：`cfg` 进了 `args`、`on_result` 进了 `local_slots_max`，要跑到对应相位
    才炸。前 7 个是身份位（bun / 权重 / 语料 / 迭代），后 12 个是可选接线与旋钮。
    """

    bun: str
    rl_path: str
    traj_dir: Path
    pairs: list[tuple[int, int]]
    args: argparse.Namespace | SimpleNamespace
    cfg: dict
    iter_id: str
    on_result: Callable[..., None] | None = None
    local_slots_max: int | None = None
    tail_dispatch: bool = True
    halt_event: threading.Event | None = None
    # 队列从「已全部派出」重新出现可派任务时回调（stream 用它重新冻结 eval）。
    on_queue_drained: Callable[[], None] | None = None
    on_queue_active: Callable[[], None] | None = None
    local_suspend: threading.Event | None = None
    extra_wver: str | None = None
    course_fp: str | None = None
    corpus_fp: str | None = None
    pool_halt: bool = False
    # 零落盘（plan/payload-zero-disk §3.1）：`common.shard.write_shard` 的 `on_receive` ——
    # `(shard 名, files, manifest)`，在**落盘之前**把字节交出去（`ShardSegments.add` 签名）。
    on_shard: Callable[[str, dict[str, bytes], dict], None] | None = None


@dataclass
class DispatchState:
    """一次 `_run` 的相位状态：配置别名 + ①②③ 的产物。

    方法之间只通过它交接；`_run` 不再持有 15 个局部别名。字段默认值只在
    「相位未跑到」时兜底，正常路径由对应相位写满。
    """

    # ---- 配置别名（`_run` 构造时填）----
    bun: str
    rl_path: str
    traj_dir: Path
    pairs: list[tuple[int, int]]
    args: Any
    cfg: dict
    iter_id: str
    on_result: Any
    local_slots_max: int | None
    halt_event: threading.Event | None
    local_suspend: threading.Event | None
    extra_wver: str | None
    course_fp: str | None
    corpus_fp: str | None
    t_queue_enter: float
    # ---- ① ping 门产物 ----
    policy: dict = field(default_factory=dict)
    task_timeout: float = 900.0
    window: float = 1800.0
    status_timeout: float = 0.0
    fail_streak_max: int = 3
    wver: str = ""
    local_bun: str = ""
    iter_no: int = 0
    meta_path: Path = field(default_factory=lambda: Path("."))
    code_hash: str = ""
    upgrade_branch: str = ""
    cfg_nodes: list[dict] = field(default_factory=list)
    nodes: list[dict] = field(default_factory=list)
    # ---- ② 权重下发产物 ----
    wkind: str = "rollout"
    weights_bytes: bytes = b""
    reuse: list[dict] = field(default_factory=list)
    need: list[dict] = field(default_factory=list)
    last_settle_at: list[float | None] = field(default_factory=lambda: [None])
    t_dist_start_box: list[float | None] = field(default_factory=lambda: [None])
    t_dist_done_box: list[float | None] = field(default_factory=lambda: [None])
    # ---- ③ 队列产物 ----
    plan_set: set[tuple[int, int]] = field(default_factory=set)
    done: set[tuple[int, int]] = field(default_factory=set)
    done_all: set[tuple[int, int]] = field(default_factory=set)
    all_tasks: list[tuple[int, int]] = field(default_factory=list)
    n_total_tasks: int = 0
    seen: set[tuple[int, int]] = field(default_factory=set)
    #: 试满尝试次数仍没结果的局（`halt_split` 的 failed 档；也是「已结算」口径的一部分）。
    missing_keys: set[tuple[int, int]] = field(default_factory=set)
    attempts: dict[tuple[int, int], int] = field(default_factory=dict)
    results: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    race_drops: dict = field(default_factory=dict)
    halted: bool = False
    local_lane: Any = None
    sum_game_sec: list[float] = field(default_factory=lambda: [0.0])
    sum_game_sec_by_node: dict[str, float] = field(default_factory=dict)


def admit_rollout_nodes(
    st: DispatchState,
    probe_results: list,
    cfg_nodes: list[dict],
    dedup_streak: dict[str, int],
) -> list[dict]:
    """① 的节点准入表：codeHash / bun major.minor / bootId / 槽位全过才进本轮。

    从 `_ping_gate` 拆出（`python.refactor.md` §P0-2）：准入判据全部来自相位状态，
    与调度器实例无关。`DEDUP_STREAK` 是跨轮模块级计数，仍按原口径直接引用。

    四道门各占一段（`_stale_code` / `_bun_gate` / `_boot_gate` / `_admitted_node`）：
    每道门都自带走人时的日志与缓存失效，主循环只负责「按原顺序问过去」。
    """
    iter_id = st.iter_id
    code_hash = st.code_hash
    local_bun = st.local_bun
    meta_path = st.meta_path
    iter_no = st.iter_no
    nodes = []
    for (nid, ping), n in zip(probe_results, cfg_nodes, strict=False):
        if ping is None:
            # ★ 2026-11-11：**不摘权重账本**。超时是「这轮够不着」，不是「节点重启丢了桶」——
            # 旧写法一次超时就 forget ⇒ 下一轮重新握手（云机 = 整份重传 0.38MB / 50s，
            # 日志里那句 `weights[rollout] -> kaggle (kept, 54.6s)` 就是它）。真重启的证据是
            # 下面的 codeHash/bun 不符；剩下的漏网（同 sha 手动重启）有 409 自愈兜底。
            log(f"[dist] node {nid}: ping failed — excluded this round（权重账本保留）")
            continue
        if ping.get("codeHash") != code_hash:
            _stale_code(nid, n, ping, st, dedup_streak)
            continue
        dedup_streak.pop(nid, None)
        if not _bun_gate(nid, ping, local_bun):
            continue
        if not _boot_gate(nid, ping, iter_id, iter_no, meta_path):
            continue
        nodes.append(_admitted_node(nid, n, ping))
    return nodes


def _stale_code(
    nid: str,
    n: dict,
    ping: dict,
    st: DispatchState,
    dedup_streak: dict[str, int],
) -> None:
    """codeHash 不符 ⇒ 版本升级/去重/告警，**本轮一律排除**（不返回布尔：没有准入路径）。

    主动升级（guarded，2026-09-01 重启循环修复②）：分支 = 训练机当前分支
    （`remote.node_upgrade.current_upgrade_branch()` 锁存）。护栏见
    `remote.node_upgrade.request_upgrade_guarded` —— 跨代去重（同节点同 (agent codeHash,
    期望 hash) 只杀一次，F1：键含期望 hash）。git 脏树拒发已随 2026-11-27 去 git 裁定移除。
    F2：mismatch 分支所有日志携带两侧 hash 前缀。
    """
    code_hash = st.code_hash
    upgrade_branch = st.upgrade_branch
    # codeHash 不符 ⇒ 节点可能 pull/restart 丢权重——缓存必须失效
    common.distribution.forget_weights_node(nid)
    local_short = code_hash[:8]
    remote_short = str(ping.get("codeHash") or "")[:8] or "none"
    if not upgrade_branch:
        log(
            f"[dist] node {nid}: codeHash mismatch "
            f"(local={local_short} remote={remote_short}) — excluded (red, no upgrade branch)"
        )
        dedup_streak.pop(nid, None)
        return
    ok, reason = remote.node_upgrade.request_upgrade_guarded(
        remote.node_upgrade.UpgradeAsk(
            nid=nid,
            url=n["url"],
            auth_key=n.get("authKey", ""),
            branch=upgrade_branch,
            ping_hash=str(ping.get("codeHash")),
            expected_hash=code_hash,
        )
    )
    if remote.node_upgrade.is_self_node(n["url"], nid):
        log(
            f"[dist] node {nid}: self node stale "
            f"(local={local_short} remote={remote_short}) — restart-only "
            f"upgrade ({reason}), no git pull; excluded this round"
        )
    elif reason == "restart-requested":
        log(
            f"[dist] node {nid}: requested upgrade to {upgrade_branch} "
            f"(accepted) — node will git pull then restart"
        )
    elif reason == "dedup":
        # F6：连续 ≥3 轮 dedup 且非 self ⇒ 显式告警——dedup 是静默排除，
        # mac 事故 40+ 轮无任何提示；指引用 codehash-report 双侧 diff 定位。
        dedup_streak[nid] = dedup_streak.get(nid, 0) + 1
        log(
            f"[dist] node {nid}: codeHash mismatch "
            f"(local={local_short} remote={remote_short}) — restart already "
            f"sent for this agent codeHash (dedup) — excluded this round"
        )
        if not remote.node_upgrade.is_self_node(n["url"], nid) and dedup_streak[nid] >= 3:
            log(
                f"[dist] WARN node {nid}: stale for {dedup_streak[nid]} "
                f"rounds, upgrade suppressed by dedup （去重有"
                f"{int(remote.node_upgrade.restart_dedup_cooldown_sec())}s 冷却窗，"
                f"窗过后会自动重发一次）— 在节点执行 "
                f"'bun tools/agent/codehash-report.ts' 与本机 diff"
                f"（见 plan/dist-codehash-stale-fix.md §4）"
            )
    else:
        log(
            f"[dist] node {nid}: codeHash mismatch "
            f"(local={local_short} remote={remote_short}) — upgrade request "
            f"failed ({reason}) — excluded this round"
        )
    if reason != "dedup":
        dedup_streak.pop(nid, None)


def _bun_gate(nid: str, ping: dict, local_bun: str) -> bool:
    """bun 版本门：major.minor 必须一致（patch 差只黄不红）；红则权重账本失效。"""
    remote_full = str(ping.get("bunVersion", "?"))
    if mm(remote_full) != mm(local_bun):
        log(
            f"[dist] node {nid}: bun {remote_full} vs local {local_bun} "
            f"(major.minor differs) — excluded (red)"
        )
        common.distribution.forget_weights_node(nid)
        return False
    if remote_full != local_bun:
        log(f"[dist] node {nid}: bun patch differs ({remote_full} vs {local_bun}) — allowed (yellow)")
    return True


def _boot_gate(nid: str, ping: dict, iter_id: str, iter_no: int, meta_path: Path) -> bool:
    """本轮 bootId 钉（plan/sampler-single-instance.plan.md §8-Q2）。

    **本轮**第一次看到的 bootId 就是钉子，之后（回场 rescan / 重探）不一致 ⇒ 这个端口上
    换过进程 ⇒ 本轮排除。比 codeHash 门更早：两个 agent 同听一个端口时 codeHash 门是抽签，
    bootId 变化看得见。
    """
    boot_why = node_identity.note_ping(node_identity.round_key("rollout", iter_id), nid, ping)
    if not boot_why:
        return True
    log(f"[dist] node {nid}: {boot_why} — excluded this round (stale bootId)")
    _record_agent_meta(
        meta_path,
        {
            "node": nid,
            "mode": "rollout",
            "it": iter_no,
            "ok": False,
            "reason": boot_why,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    return False


def _admitted_node(nid: str, n: dict, ping: dict) -> dict:
    """过完四道门的节点行（槽位 = 显式 config / 自报池容量，见下）。"""
    # 派工槽位（2026-11-14 收口，plan/node-throughput-and-keepalive P1）：显式 config 是
    # 操作者旋钮（控制台「并发」），缺省回落节点自报池容量 `workers` → `cpus`。
    # 不再与核数取小——派工线程每局被隧道往返与回传整段占住（wall 9.3s / svc 2.3s），
    # 按核数取小会让节点 2 个游戏槽 64% 时间空转。超额认购的防线交给 agent
    # 准入队列（有界排队 / 503），多出的线程是**排队**，不是故障。
    c_n, _online = node_report.node_online(nid, n, ping)
    log(_online)
    return {
        "id": nid,
        "url": n["url"],
        "key": n.get("authKey", ""),
        "c": c_n,
        "ping": ping,  # M1d：能力位查询（stageJsonSupport）
    }

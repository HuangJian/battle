"""common/distribution_limits.py — 节点探活 / 并发预算（纯函数，stdlib-only）。

从 `common/distribution.py` 下沉（2026-11-11 LOC 预算）：这两个口径是独立的
节点限制，派发、评估、批跑、扩容共用；`distribution` 保留同名门面。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: 节点探活（`/v1/ping`）的**唯一**缺省预算（秒）。
#: 2026-11-11 曾抬到 8s（§148，判词是「云机空载应答 0.9~2.7s，3s 太紧」）——**真因不是
#: 预算紧**：`socket.create_connection` 串行试 `getaddrinfo` 的每个地址，且 timeout 是
#: **每地址**的。本机无可用 IPv6 路由 ⇒ kaggle 一次 ping 的 tcp 段 16.24s（IPv4 只要
#: 0.21s）。抬预算只是把这笔税从 6s 放大到 16s：同批课程 rollout 从 39~41s 变成 53~95s。
#: connect 竞速（`common.net_http.racing_create_connection`）修好后收回 3s，见 §149。
STATUS_TIMEOUT_SEC: float = 3.0


def status_timeout_of(policy: Mapping[str, Any] | None) -> float:
    """`policy.statusTimeoutSec` → 探活预算（缺席/坏值/非正 ⇒ `STATUS_TIMEOUT_SEC`）。

    单一入口：派发（rollout）、评估、批跑三处都读它——两处各写一个缺省字面量，
    改一处漏一处就是本仓库的老事故型（先例见 `codehash-files.txt` 的双语同步）。
    """
    raw = (policy or {}).get("statusTimeoutSec")
    if raw is None:
        return STATUS_TIMEOUT_SEC
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return STATUS_TIMEOUT_SEC
    return v if v > 0 else STATUS_TIMEOUT_SEC


def node_slots_note(configured: Any, cpus: Any, workers: Any) -> str:
    """`[dist] node … online` 行的后缀：config/workers/cpus + 超额派工提示。

    超额（`config > 2×池容量`）是**排队**不是故障（见 `node_slots`）——但值得一行，
    否则「派了 8 条却只跑 2 局」看起来像丢任务。
    """
    cfg = int(configured or 0)
    cap = max(1, int(workers or cpus or 1))
    note = f"（config={cfg} workers={workers} cpus={cpus}）"
    if cfg > 2 * cap:
        note += " 超额派工：多出的线程在准入队列等待（waitMs=12s / qMax=32）"
    return note


def node_slots(configured: Any, cpus: Any, workers: Any = None) -> int:
    """节点派工槽位：显式 `concurrency` 是操作者旋钮，**不再被节点核数取小**。

    2026-11-14 收回 2026-11-11 的「`min(config, ping.cpus)`」——它把「节点能同时跑几个
    游戏进程」误当成「派工线程该有几个」，而两者不是一回事：派工线程每局会被**隧道往返
    与结果回传**整段占住（gcs 实测 `wall=9.3s` / 节点服务 `elapsed=2.3s`），要几条线程
    叠起来才能把节点 CPU 喂满。按核数取小的后果是每台云机只派 2 条线程 ⇒ 2 个游戏槽
    64% 时间空转（槽位占用 36% = 20 局/分 × 2.16s ÷ (2 槽 × 60s)；用户 2026-11-14 报障）。

    「超额认购把父进程饿死」的防线改由**节点自己**承担：agent 准入队列按 `workers`
    （缺省 = 物理核）发牌、池满有界排队/503，它才知道真实容量；`ping.workers` 这里只做
    观测与缺省回落。故「没人手填就按核数」的口径不变（`configured` 缺省时回落
    `workers` → `cpus` → 1），有人手填就照填——UI 上那个「并发 N」必须真的生效。
    """
    cfg = int(configured or 0)
    if cfg > 0:
        return cfg
    rep = int(workers or 0) or int(cpus or 0)
    return max(1, rep or 1)

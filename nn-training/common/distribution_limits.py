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


def node_slots(configured: Any, cpus: Any) -> int:
    """节点并发槽位 = `min(config concurrency, 节点自报 cpus)`（任一缺 ⇒ 用另一个）。

    为什么取小：`/v1/ping.cpus` 是 `physicalCores()`（min(物理核, cgroup 配额, 掩码)），
    而 config 里的 `concurrency` 是人手填的旧值——kaggle 会话只给 2 个物理核，
    config 却钉着 4 ⇒ 4 个重型 native worker 抢 2 个核，父进程的事件循环被饿住，
    于是 1s 的 HTTP 变成 30~60s（2026-11-11：权重握手 54.6s、ping 成批超时）。
    """
    cfg = int(configured or 0)
    rep = int(cpus or 0)
    if cfg > 0 and rep > 0:
        return max(1, min(cfg, rep))
    return max(1, cfg or rep or 1)

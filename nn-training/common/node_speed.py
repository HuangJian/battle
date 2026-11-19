"""common/node_speed.py — 节点派工顺序的速度表（纯函数 + 小窗口，L0）。

住在 `common/` 而不是 `trainer/`：它不碰传输层也不是编排（分层守卫
`tests/test_layering.py` 要求 `trainer/` 只放编排模块），先例 = `distribution_limits` /
`race_metrics`。

背景（plan/node-throughput-and-keepalive D3，用户 2026-11-14 裁定）：采样线程按每节点最近
K 局 `wallSec` 的均值**升序**启动，快节点先拿首波任务；**不减槽**——慢节点仍拿满自己的
`c_cap`，尾部交给既有 tail-race（`pick_race_target`）兜底。

数据源**不新增落盘文件**：每次排产从 `dist-agent-meta.jsonl` 尾部现读一次（结算时会追加
`wallMs`），不驻留内存 —— 重跑/重连后顺序不会丢，也不会和盘上（`record`）两本账。
`wallSec` 是训练机侧「派发→结算」墙钟（含网络/准入队列），正是「从训练侧看这个节点多快」
的那个量；不要换成 `elapsedSec`（节点服务时长，不含往返）。

生效面（诚实记录）：worker 从**同一个 `pending` 队列**抢活，所以启动顺序只影响开轮第一波
任务的归属；`need` 节点的线程是在权重 POST 完成回调里孵化的（并行、完成序不确定），那部分
排序是尽力而为。收益因此低于「换调度器」，验收只要求「不劣化 + 观测可见」。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any

#: 窗口长度（局）：用户 2026-11-14 裁定 K=50。
K_DEFAULT = 50
#: 冷启动门槛（局）：样本少于它就用全体中位数参与排序（用户 2026-11-14 裁定）。
MIN_SAMPLES = 5
#: 播种时从 meta 文件尾部最多读几行（够 50×每节点数，且不会把几十万行读进内存）。
SEED_MAX_LINES = 4000


class SpeedWindow:
    """每节点最近 K 局 `wallSec` 的滑动窗口（坏值直接丢，不污染均值）。"""

    __slots__ = ("_q",)

    def __init__(self, k: int = K_DEFAULT) -> None:
        self._q: deque[float] = deque(maxlen=max(1, int(k)))

    def add(self, wall_sec: Any) -> None:
        try:
            v = float(wall_sec)
        except (TypeError, ValueError):
            return
        if v > 0:
            self._q.append(v)

    def avg(self) -> float | None:
        return sum(self._q) / len(self._q) if self._q else None

    def n(self) -> int:
        return len(self._q)


def parse_meta_rows(lines: Iterable[str]) -> dict[str, list[float]]:
    """meta 行 → `{node: [wallSec, …]}`：只取 `mode=rollout ∧ ok=true`；坏行/缺字段跳过。

    为什么不抛：训练机的 meta 可能被并发写坏（历史上有行首被覆盖只剩尾部、孤立 `}`
    的记录，见 docs/nn/runtime-opt.md），一条坏行不该让整轮排序失效。
    """
    out: dict[str, list[float]] = {}
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if row.get("mode") != "rollout" or row.get("ok") is not True:
            continue
        node = row.get("node")
        if not isinstance(node, str) or not node:
            continue
        raw = row.get("wallSec")
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.setdefault(node, []).append(v)
    return out


def seed_from_meta(
    path: Any, k: int = K_DEFAULT, max_lines: int = SEED_MAX_LINES
) -> dict[str, SpeedWindow]:
    """从 meta 文件**尾部**读一次播种（文件缺失/读不动 ⇒ 空表，绝不抛）。"""
    windows: dict[str, SpeedWindow] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=max(1, int(max_lines)))
    except OSError:
        return windows
    for node, vals in parse_meta_rows(tail).items():
        w = SpeedWindow(k)
        for v in vals[-max(1, int(k)) :]:
            w.add(v)
        windows[node] = w
    return windows


def _median_of(vals: list[float]) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def order_table(
    nodes: Iterable[str], windows: Mapping[str, SpeedWindow], min_samples: int = MIN_SAMPLES
) -> dict[str, float]:
    """排序键表：样本 ≥ `min_samples` 用自身均值，其余用**全体中位数**。

    全表都没有可用样本 ⇒ 空表（调用方保持配置序，不发明顺序）。
    """
    ids = list(nodes)
    known: dict[str, float] = {}
    for nid in ids:
        w = windows.get(nid)
        if w is not None and w.n() >= min_samples:
            avg = w.avg()
            if avg is not None:
                known[nid] = avg
    med = _median_of(list(known.values()))
    if med is None:
        return {}
    return {nid: known.get(nid, med) for nid in ids}


def order_nodes(
    nodes: Iterable[str], windows: Mapping[str, SpeedWindow], min_samples: int = MIN_SAMPLES
) -> list[str]:
    """按速度**升序**返回节点 id（快节点先拿首波；并列/无数据保持原序 —— stable sort）。"""
    ids = list(nodes)
    table = order_table(ids, windows, min_samples)
    if not table:
        return ids
    return sorted(ids, key=lambda nid: table[nid])


def order_batches(
    nodes: Iterable[str],
    reuse: list[dict],
    need: list[dict],
    windows: Mapping[str, SpeedWindow],
    min_samples: int = MIN_SAMPLES,
) -> tuple[list[dict], list[dict], str]:
    """按速度重排 `reuse` / `need`（**不减槽**），并返回一行观测文本。

    无可用样本 ⇒ 原样返回、文本为空（调用方不打印，保持旧顺序）。
    """
    order = {nid: i for i, nid in enumerate(order_nodes(nodes, windows, min_samples))}
    tail = len(order)
    reuse = sorted(reuse, key=lambda nd: order.get(nd["id"], tail))
    need = sorted(need, key=lambda nd: order.get(nd["id"], tail))
    line = " → ".join(
        f"{nd['id']} {wall_note(windows, nd['id'])}" for nd in reuse + need
    )
    return reuse, need, line


def order_round(
    nodes: list[dict],
    reuse: list[dict],
    need: list[dict],
    meta_path: Any,
    policy: Mapping[str, Any],
    log: Callable[[str], None],
) -> tuple[list[dict], list[dict]]:
    """现读 meta 尾部 → 重排本轮派工 → 打一行 `[dist] node order:`。

    开关 = `policy.nodeOrderBySpeed`（缺省 1）；关闭 / 无节点 ⇒ 原样返回、不打行（回退旋钮 =
    纯配置序）；文件缺失/无样本 ⇒ 行里如实写 `wall=无样本 n=0`，顺序仍是配置序。
    """
    if not (nodes and int(policy.get("nodeOrderBySpeed", 1) or 0)):
        return reuse, need
    ids = [nd["id"] for nd in nodes]
    reuse, need, line = order_batches(ids, reuse, need, seed_from_meta(meta_path))
    log(f"[dist] node order: {line}")
    return reuse, need


def wall_note(windows: Mapping[str, SpeedWindow], node: str) -> str:
    """日志用读数：`wall=14.9s/局 n=50`（无样本/样本不足如实说）。"""
    w = windows.get(node)
    if w is None or w.n() == 0:
        return "wall=无样本 n=0"
    avg = w.avg() or 0.0
    tag = "" if w.n() >= MIN_SAMPLES else "（冷启动，取中位数）"
    return f"wall={avg:.1f}s/局 n={w.n()}{tag}"

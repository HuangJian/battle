"""remote/wire.py — worker 的**传输账 / 低速重抽 / bulk 节流**（S4 拆分，2026-09-23）。

从 `remote/worker.py` 整簇搬出来的**无类**关注点：`WIRE_*` 阈值 · 每 job 传输账（`_WIRE`）·
本会话最好速率（`_BEST_RATE`）· bulk 单通道调度器（`_BULK`），以及 `_wire_*` /
`_note_rate` / `_min_rate` / `_reroll_decision` / `set_bulk_log` / `_bulk_pace`。

## 为什么**状态随簇搬迁**（而不是留宿主让本模块 import）

顶层函数与模块全局同生共死。若把状态留在 `worker.py` 而让本模块 import 回来，就会
得到**两份账**：`worker._WIRE` 与 `wire._WIRE` 各自增长，而 `_wire_flush` 只读后者
——静默的读数损失（本仓 S4 前几步反复撞上的那类故障）。所以本模块是这三份状态的
**唯一所有者**，`remote/worker.py` 只做 `from remote.wire import … as …` 的显式转发，
于是 `remote.worker._WIRE` 等名字仍然存在（测试与 `worker_server` 的注入点不变）。

## 注入点（拆分会**静默**坏掉的东西）

* `_BEST_RATE` 是**会话级标量**——`_note_rate` 用 `global` **重绑**它。测试若
  `worker._BEST_RATE = 0.0`，重绑的只是转发名，`_min_rate` 读到的仍是本模块那份
  ⇒ 注入点必须指向**本模块**（`remote.wire._BEST_RATE`）。
* `_WIRE` / `_BULK` 是**原地可变**（`.clear()` / `.reset()`）：转发名指向同一个对象，
  从哪个入口进都一样。
* `_BULK` 同时被 `worker.py` 的传输核心（`_request` / `_get_with_retry` / `post_result`）
  使用——同一实例经转发共享，`sched0` 增量（`s1 - s0`）才不会永久失真。
"""

from __future__ import annotations

import time
from typing import Any

from remote.bulk_sched import BulkScheduler

__all__ = [
    "WIRE_MAX_JOBS",
    "WIRE_MIN_RATE",
    "WIRE_PROBE_BYTES",
    "WIRE_PROBE_SEC",
    "WIRE_RATE_SAMPLE_MIN_BYTES",
    "WIRE_REROLL_BUDGET_SEC",
    "WIRE_REROLL_MAX",
    "_BEST_RATE",
    "_BULK",
    "_WIRE",
    "WireSlowError",
    "_bulk_pace",
    "_min_rate",
    "_note_rate",
    "_reroll_decision",
    "_wire_add",
    "_wire_block",
    "_wire_bucket",
    "_wire_flush",
    "_wire_hit",
    "_wire_note_reroll",
    "_wire_start",
    "_wire_time",
    "set_bulk_log",
]

# ── M0 统一计量：worker 侧 `wire` 子字典（2026-09-24 从 `worker.py` 下沉）──
def _wire_block(**over: object) -> dict:
    """M0 统一计量：worker 侧 `wire` 子字典（全 additive——旧 hub 的 validate_result
    不校验未知字段，旧读方忽略）。over 里 None 的键保留默认值（不把缺失写成 null）。"""
    w: dict = {
        "payload_bytes": 0,
        "payload_dl_sec": 0.0,
        "unpack_sec": 0.0,
        "opt_restore_sec": 0.0,
        "grad_sec": 0.0,
        "blob_hits": 0,
        "blob_miss_bytes": 0,
        "result_bytes": 0,
        # M3 kind=iter（其余 job 恒 0/False = 本轮没走这条线）；rollout_sec / bun_version
        # 不在这里给默认值——缺席就代表「本轮没有节点侧 rollout」，不能写成 0 冒充。
        "ts_code_bytes": 0,
        "ts_code_hit": False,
    }
    w.update({k: v for k, v in over.items() if v is not None})
    return w


# ── 低速重抽（2026-09-20；plan/minimize-payload.plan.md §4.0 / M1）───────
#: 坏签（连接抽签抽到慢连接）时**主动断开重发**：重抽成本 ≈1 s 建连，收益 ≈100 s。
#: 实测依据（同机同 hub）：code GET 13.5 KB/s，而 3 秒后的 blob GET ≥120 KB/s；
#: 12:59 那次 payload 以 6–9 KB/s 烧完 300 s 预算（到 75% 被总预算判死），
#: **重试换连接后 354 KB/s** 跑完剩下 1.75 MB——那次重试其实就是一次「意外重抽」。
WIRE_MIN_RATE = 80 * 1024.0
#: 判据所需的最小观测：样本太小不下结论。
WIRE_PROBE_BYTES = 128 * 1024
WIRE_PROBE_SEC = 3.0
#: 按当前速率**预计剩余**超过它才值得折腾（快跑完了就不动）。
WIRE_REROLL_BUDGET_SEC = 20.0
#: 单次下载的重抽上限（**只给幂等 GET**；POST result 永不重抽）。
WIRE_REROLL_MAX = 3
#: 会话最好速率的采样最小体量（小 body 的瞬时速率不代表链路，不参与判据）。
WIRE_RATE_SAMPLE_MIN_BYTES = 256 * 1024
#: 未 flush 的 job 传输账上限（push 模式没有 pull 循环的 flush 点）。
#: 2026-09-22（P2.5 异步回传）：async 模式下 flush 被推迟到回传落定，于是同时在册的
#: bucket = 在飞回传（≤ 队列深度）+ 当前 job + 预取的合成 id ≈ 4。**留一倍余量**——
#: 溢出会按最旧丢，那一刻丢掉的是一整行阶段账（静默的读数损失）。
WIRE_MAX_JOBS = 8

#: 本会话已观测到的最好大 body 速率（bytes/s）——相对判据的参照。
_BEST_RATE = 0.0

#: 每 job 的传输账：jid -> {segs: {段名: [bytes, sec]}, hits: {段名: 说明},
#: wasted: 重抽作废字节, rerolls: 次数, sched0: 建账时的调度器快照}。
#: 跑完由 `_wire_flush` 打一行摘要并清空。
_WIRE: dict[str, dict] = {}

#: bulk 单通道调度器（plan/transfer-scheduling §2.2 / P0）：**一个进程一份**。
#: 三条流的分工（控制面永不排队 / P1 至多一条在途且不被抢断 / P2 可打断）全在
#: `remote/bulk_sched.py` 里；这里是它在本模块的落点：
#:   · 控制面 —— `_request` 按**路径**分流（`control_path`），命中即标记让路；
#:   · P1 —— `post_result` 与开算前的关键下载（`download_*` 缺省即 P1）；
#:   · P2 —— 预取下载（`download_payload(bulk_prio=BULK_P2_PREFETCH)`）。
_BULK = BulkScheduler()


def set_bulk_log(log: Any) -> None:
    """把 worker 的日志函数交给调度器（排队/让路/抢占要能在现场日志里看见）。"""
    _BULK.set_log(log)


def _bulk_pace(token: int, prio: str) -> Any:
    """分片间隙的让路回调（交给 `_read_body`）：控制面在途 ⇒ 暂停；P2 被挤 ⇒ 中断。"""

    def _pace() -> None:
        _BULK.pace(token, prio)

    return _pace


def _note_rate(rate: float, nbytes: int) -> None:
    """记下本会话的**大 body** 最好速率（相对判据的参照）。"""
    global _BEST_RATE
    if nbytes >= WIRE_RATE_SAMPLE_MIN_BYTES and rate > _BEST_RATE:
        _BEST_RATE = rate


def _min_rate() -> float:
    """坏签判据 = max(`WIRE_MIN_RATE`, 本会话最好速率 / 4)——只在「明显偏离」时动手。"""
    return max(WIRE_MIN_RATE, _BEST_RATE / 4.0)


def _reroll_decision(
    got: int,
    total: int,
    elapsed: float,
    *,
    min_rate: float | None = None,
    budget_sec: float = WIRE_REROLL_BUDGET_SEC,
    probe_bytes: int = WIRE_PROBE_BYTES,
    probe_sec: float = WIRE_PROBE_SEC,
) -> tuple[bool, float, float]:
    """是否该断开重抽 → `(决定, 实测速率, 按此速率的预计剩余秒数)`。

    **纯函数**：判据只有一处实现（改阈值只改这里），也就能被单测直接钉住。
    """
    if total <= 0 or got <= 0 or got >= total:
        return False, 0.0, 0.0  # 长度未知 / 已收完 / 零字节：都不判
    if elapsed < probe_sec and got < probe_bytes:
        return False, 0.0, 0.0  # 样本太小，不下结论
    rate = got / elapsed if elapsed > 0 else float("inf")
    floor = _min_rate() if min_rate is None else min_rate
    remain_sec = (total - got) / rate if rate > 0 else float("inf")
    return (rate < floor and remain_sec > budget_sec), rate, remain_sec


class WireSlowError(Exception):
    """慢连接（速率远低于阈值）：**放弃本次传输、换连接重抽**。

    带正文（已收字节数 / 速率 / 预计剩余）——与裸 `TimeoutError()` 的教训同规：日志里必须
    能读出「为什么断」，否则运维只看到「又重试了」。
    """

    def __init__(self, bytes_read: int, rate: float, remain_sec: float) -> None:
        super().__init__(
            f"慢连接：实测 {rate / 1024:.0f} KB/s（已收 {bytes_read} bytes，"
            f"按此速率剩余 {remain_sec:.0f}s）"
        )
        self.bytes_read = bytes_read
        self.rate = rate
        self.remain_sec = remain_sec


def _wire_bucket(jid: str) -> dict:
    """取（或建）某 job 的传输账——并**封顶**未 flush 的 job 数（防 push 模式无界增长）。"""
    w = _WIRE.get(jid)
    if w is None:
        while len(_WIRE) >= WIRE_MAX_JOBS:
            _WIRE.pop(next(iter(_WIRE)), None)  # 最旧的直接丢（它的账已过期）
        w = _WIRE[jid] = {
            "segs": {},
            "hits": {},
            "times": {},  # 只有秒数的段（`ppo`）：阶段占比要用
            "wasted": 0,
            "rerolls": 0,
            "sched0": _BULK.stats(),  # 本 job 起点的调度器快照（flush 时算增量）
            "t0": time.time(),  # 建账时刻（claim 时会被 `_wire_start` 重写）
        }
    return w


def _wire_start(jid: str) -> None:
    """标一个 job 的**起点**（claim 成功那一刻）：阶段占比的 `wall` 从它起算。

    不标也不会丢账（建账时自带 t0），但那样 `wall` 从「第一段传完」起算，会系统性
    少报排队/装载那一段——而那一段正是 P0.5 要看的「GPU 空转」主体。
    """
    if not jid:
        return
    _wire_bucket(jid)["t0"] = time.time()


def _wire_add(jid: str, seg: str, nbytes: int, sec: float) -> None:
    """记一段**成功传输**（`(endpoint, bytes, sec)` 的原始账）。"""
    if not jid or not seg or nbytes <= 0:
        return
    cur = _wire_bucket(jid)["segs"].setdefault(seg, [0, 0.0])
    cur[0] += int(nbytes)
    cur[1] += float(sec)


def _wire_time(jid: str, seg: str, sec: float) -> None:
    """记一段**只有秒数**的账（`ppo`）：P0.5 的 `T_in / T_out / T_ppo / other` 占比靠它。"""
    if not jid or not seg or sec < 0:
        return
    b = _wire_bucket(jid)
    b["times"][seg] = float(b["times"].get(seg, 0.0)) + float(sec)


def _wire_hit(jid: str, seg: str, why: str = "cache") -> None:
    """记一次**零字节**命中（内容寻址缓存 / preloaded）——摘要里也要看得见。"""
    if not jid or not seg:
        return
    _wire_bucket(jid)["hits"][seg] = why


def _wire_note_reroll(jid: str, wasted: int) -> None:
    """记一次重抽（及其作废字节）——坏签比例就靠它统计。"""
    if not jid:
        return
    w = _wire_bucket(jid)
    w["rerolls"] += 1
    w["wasted"] += int(wasted)


def _wire_flush(jid: str, log, *, wall_end: float | None = None) -> None:
    """打**一行**本 job 的传输账并清掉：`wire payload=… code=cache-hit reroll=1 合计=…`。

    为什么必须有一行：逐条进度行看不出全局，而「坏签比例 / 命中比例 / 哪一段在吃时间」
    只能从每 job 一行的账里读（与 hub 侧 `_bytes` 的完成行对账即可定位慢腿）。

    `wall_end`（P2.5 异步回传）：本 job **关键路径的终点**（结果就绪那一刻）。给了它
    就表示回传是在关键路径**之外**跑的，于是：`wall` 只算到那一刻、`out` 全额记进
    `overlap=`（**不**从账上抹掉——那会让人以为回传不花钱）。缺省（sync 模式或旧调用点）
    = 现在，与改造前的口径逐字相同。
    """
    w = _WIRE.pop(jid, None)
    if not w:
        return
    mb = 1024.0 * 1024.0
    parts: list[str] = []
    tot_b = 0
    tot_s = 0.0
    for seg, (n, sec) in w["segs"].items():
        tot_b += n
        tot_s += sec
        rate = n / sec / 1024.0 if sec > 0 else 0.0
        parts.append(f"{seg}={n / mb:.2f}MB/{sec:.1f}s({rate:.0f}KB/s)")
    for seg, why in w["hits"].items():
        parts.append(f"{seg}={why}-hit")
    if w["rerolls"]:
        parts.append(f"reroll={w['rerolls']}(wasted {w['wasted'] / mb:.2f}MB)")
    # 调度账（§2.2/P0）：本 job 期间在 bulk 队列上等了多久、让路几次、控制面往返多快。
    # `p0_rt_ms_p95` 是**会话级**读数（分位数不能做增量），其余按 job 起点快照取差。
    s0 = w.get("sched0") or {}
    s1 = _BULK.stats()
    wait = float(s1.get("queue_wait_sec", 0.0)) - float(s0.get("queue_wait_sec", 0.0))
    yields = int(s1.get("yield_count", 0)) - int(s0.get("yield_count", 0))
    parts.append(
        f"wait={wait:.1f}s/yield={yields}/p0_p95={float(s1.get('p0_rt_ms_p95', 0.0)):.0f}ms"
    )
    # 阶段占比（P0.5 基线）：T_in = 一切下载（payload/code/blob/预取），T_out = 结果回传，
    # T_ppo = 计算，other = 其余（排队/装载/解包/落盘）——「GPU 空转」主要就落在这里。
    # 这一行的用途是**判 P2 盈亏**：预取只值得做在「T_in 占比高 且 other 里有等下载」的现场。
    for seg, sec in sorted((w.get("times") or {}).items()):
        parts.append(f"{seg}={float(sec):.1f}s")
    ppo_sec = float((w.get("times") or {}).get("ppo", 0.0))
    out_sec = float(w["segs"].get("result", (0, 0.0))[1])
    in_sec = tot_s - out_sec
    # 关键路径终点：async 回传时是「结果就绪」那一刻（回传在它之后才发生，且与下一份
    # job 并发）；缺省 = 现在（含回传本身）。
    crit_end = float(wall_end) if wall_end is not None else time.time()
    # 关键路径里**不该**包含 `out`（async）：它已经与下一份 job 重叠了。`overlap` 把
    # 它如实报出来——判据是「in + ppo + other ≈ wall」，不是「out 消失了」。
    crit_out = 0.0 if wall_end is not None else out_sec
    overlap = out_sec - crit_out
    # `wall` 取「实测墙钟」与「各阶段之和」的较大者：阶段是**已测量的区间**，
    # 墙钟不可能比它们之和小（时钟粒度/人工拼接会给出略小的读数），而一个比
    # 各阶段之和还小的分母会把占比算成荒谬值（读表的人会以为自己在看噪声）。
    wall = max(crit_end - float(w.get("t0") or crit_end), in_sec + crit_out + ppo_sec)
    other = max(wall - in_sec - crit_out - ppo_sec, 0.0)
    rate_all = tot_b / tot_s / 1024.0 if tot_s > 0 else 0.0
    log(
        f"job {jid}: wire "
        + " ".join(parts)
        + f" 合计={tot_b / mb:.2f}MB/{tot_s:.1f}s({rate_all:.0f}KB/s)"
        + f" phases in={in_sec:.1f}s out={out_sec:.1f}s ppo={ppo_sec:.1f}s"
        f" other={other:.1f}s wall={wall:.1f}s overlap={overlap:.1f}s"
    )

"""轮中自救（池化版）：扩容 / 停派后回场 / 轮中本机兜底（2026-11-01；2026-11-04 池化）。

任务级仲裁（`worker/eval_arbiter.py`）之后，消费线程住进程级池（`worker/eval_exec.py`）——
本模块不再孵化/持有线程，只保留三条救路与一个收口等待：

* `round_live`：这一轮是否还有「可路由节点 + 活线程」（自救判据）；
* `grow_pools`：rollout 退场后重 ping 让过位的节点，把空出来的槽补成线程；
* `rescue_round`：有活却无路可走时 ① 兜底模式本机接盘一次 ② 软失败停派节点退避后回场
  ③ 节点让过位 ⇒ 留在窗口里等扩容；都无路 ⇒ `"workers-gone"`；
* `supervise_round`：等这一轮收口（`settled` / `window` / `workers-gone`）。

现场（2026-11-01，z3-p2002ctl it25/it50）：三节点 `503 {"error":"busy"}` 打满
`nodeSoftFailStreak` → 消费线程全部退出，主循环判死 786/800 局，而窗口还剩 1776s。
等一等集群就空出来了，缺的只是「谁去再取一次」——池化后线程不退出，救援变成
「给这一轮重新找一条能取任务的路」。

判据公式仍在 `worker/eval_yield.py`（`eval_slot_cap` / `local_fallback_midround` /
`soft_comeback_due` / `SOFT_COMEBACK_MAX`）：本模块只做调用与账本更新。
"""

from __future__ import annotations

import time
from typing import Any

import common.distribution
from worker.eval_arbiter import LOCAL_NODE, EvalRound
from worker.eval_yield import (
    SOFT_COMEBACK_MAX,
    eval_slot_cap,
    local_fallback_midround,
    soft_comeback_backoff_sec,
    soft_comeback_due,
)

#: 动态扩容的重 ping 间隔（秒）；`0` = 关闭。判据是「rollout 退场后补线程」而不是墙钟：
#: 测试把它压到毫秒级，走的仍是同一条 `grow_pools` 路。
EVAL_SLOT_GROW_SEC = 15.0
#: 扩容 ping 的每节点预算 = 派发侧同一个缺省（`common.distribution.STATUS_TIMEOUT_SEC`，
#: 2026-11-11 由 3s 抬到 8s：云机隧道空载就要 0.9~2.7s，3s 在忙时必超时）。
EVAL_GROW_PING_TIMEOUT_SEC = common.distribution.STATUS_TIMEOUT_SEC
#: 收口轮询步长（秒）：只是定时醒来重判，正常路径靠事件/计数。
SUPERVISE_TICK_SEC = 0.2


def round_live(rnd: EvalRound, execs: Any, now: float | None = None) -> bool:
    """这一轮还有没有「取得到任务」的活线程（节点须可路由，本机须参与且已孵）。"""
    now = time.time() if now is None else float(now)
    for nid in rnd.node_ids():
        if rnd.node_routable(nid, now) and execs.live(nid) > 0:
            return True
    return rnd.local_ok(now) and execs.live(LOCAL_NODE) > 0


def grow_pools(rnd: EvalRound, execs: Any) -> int:
    """rollout 退场 ⇒ 重 ping，把空出来的槽补成这一轮的线程（2026-11-03）。

    只补**派发时让过位**的节点（`c < baseC`）：没让位 = 本来就满额，重 ping 纯属多余。
    `ping.inflight` 含我们自己的在飞局 ⇒ 先减掉本节点池里的活线程，剩下的才是
    rollout / 别的轮占的份额，再走与派发时同一个 `eval_slot_cap` 判据。ping 拿不到
    就这拍不扩，不抛。
    """
    yielded = rnd.yielded_nodes()
    if not yielded:
        return 0
    try:
        pings = common.distribution.ping_nodes_parallel(
            yielded, timeout=EVAL_GROW_PING_TIMEOUT_SEC
        )
    except Exception:
        return 0
    added = 0
    queue_len = rnd.pending_len()
    for nd, ping in zip(yielded, pings, strict=True):
        nid = str(nd["id"])
        if ping is None or not ping.get("evalSupport"):
            continue
        if not rnd.node_routable(nid, time.time()):
            continue
        alive_n = execs.live(nid)
        base_c = int(nd.get("baseC") or nd.get("c") or 1)
        others = max(0, int(ping.get("inflight") or 0) - alive_n)
        want = eval_slot_cap(base_c, others)
        add = want - alive_n
        if add <= 0:
            continue
        added += execs.ensure_node({**nd, "c": want}, want)
        rnd.log(
            f"[eval] it{rnd.it}: node {nid} 扩容 +{add} → {want} 线程"
            f"（inflight={ping.get('inflight')}，队列还剩 {queue_len} 局）"
        )
    return added


def rescue_round(rnd: EvalRound, execs: Any) -> str:
    """这一轮有活却无路可走 ⇒ 自救一次；返回 `"workers-gone"` = 无路可走，应收工。

    三条救路与旧实现同序（`EvalConsumers.supervise`）：
      ① 兜底模式本机接盘一次（本轮没孵过 + 冻结快照在 + 槽位>0）；
      ② 软失败停派的节点退避到点后回场（回场 = 新一轮软失败预算）；
      ③ 节点让过位（`c<baseC`）但当下被 rollout/PPO 占满 ⇒ 留在窗口里等扩容节拍。
    真故障（`nodeFailStreak`）仍整轮停派——回场不拆节点护栏。
    """
    now = time.time()
    queue_len = rnd.pending_len()
    acted = False
    if local_fallback_midround(
        rnd.fallback_only,
        rnd.local_spawned,
        rnd.snapshot_path is not None,
        rnd.local_slots,
        True,
    ):
        rnd.mark_local_spawned()
        execs.ensure_local(rnd.local_slots)
        rnd.log(
            f"[eval] it{rnd.it}: local fallback ×{rnd.local_slots}"
            f"（节点消费线程轮中归零，队列还剩 {queue_len} 局）"
        )
        acted = True
    future_revival = False
    deadline = rnd.deadline(now)
    for nid in rnd.node_ids():
        hard_stop, soft_stop, back_n, due_at, _dl = rnd.comeback_state(nid, now)
        if hard_stop or not soft_stop or back_n >= SOFT_COMEBACK_MAX:
            continue
        if due_at is None:
            due_at = now + soft_comeback_backoff_sec(back_n)
            rnd.schedule_comeback(nid, due_at)
        if not soft_comeback_due(back_n, now, due_at, deadline):
            future_revival = future_revival or due_at < deadline
            continue
        nd = rnd.node_info(nid)
        if nd is None:
            continue
        rnd.revive_node(nid, now + soft_comeback_backoff_sec(back_n + 1))
        execs.ensure_node(nd, int(nd.get("c") or 0))
        acted = True
        rnd.log(
            f"[eval] it{rnd.it}: node {nid} 停派后回场"
            f"（第 {back_n + 1}/{SOFT_COMEBACK_MAX} 次；队列还剩 {queue_len} 局）"
        )
    if acted or future_revival:
        return ""
    if rnd.yielded_nodes():
        return ""  # 让过位、只是当下被占满：别把「等间隙」记成 workers-gone
    return "workers-gone"


def supervise_round(
    rnd: EvalRound, execs: Any, *, grow_sec: float = EVAL_SLOT_GROW_SEC
) -> str:
    """等这一轮收口，返回收工路径：`"settled"` / `"window"` / `"workers-gone"`。

    冻结（rollout 活跃）期间窗口钟停走 ⇒ `rnd.expired()` 恒假，这里只是继续等；
    新的 eval 局由仲裁器冻结，**在飞局不撤销**（用户 2026-11-04 口径）。
    """
    grow_at = time.time() + grow_sec if grow_sec > 0 else float("inf")
    while True:
        if rnd.settled.is_set():
            return "settled"
        now = time.time()
        if rnd.expired(now):
            return "window"
        has_work = rnd.has_work()
        if not has_work and rnd.writers_pending() <= 0:
            return "workers-gone"
        if grow_sec > 0 and has_work and now >= grow_at:
            grow_at = now + grow_sec
            grow_pools(rnd, execs)
        if has_work and not round_live(rnd, execs, now):
            why = rescue_round(rnd, execs)
            if why:
                return why
        time.sleep(SUPERVISE_TICK_SEC)

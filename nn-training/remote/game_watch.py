"""remote/game_watch.py —— 单局子进程的**停滞看门狗**（rollout 与 eval 共用同一套口径）。

**为什么需要它**（2026-09-22 两起实测）：

  * 云机 rollout `it34`：310/328 局在 4s 内结算完，剩下几局卡到 **651s** —— 期间日志只有
    「已结算 N 局」的计数行，谁卡了、卡了多久，一个字都没有（旧口径 `timeout=off`）；
  * 单局正常是**亚秒级**（云机 220 并发 328 局 4s；8 并发 600 局 57s）⇒ 用户口径
    「单局 >5s 肯定不正常」，所以 >5s 的局**当场杀掉原地重跑**，不留着等 651s。

一条口径五个常量，任何调用点都不许再抄一份数字：

  * ``SLOW_GAME_WARN_SEC`` (5.0) —— 点名线：超过它就打一行**带局身份**（`s3/d7`）的 WARN。
    默认硬顶与它同值 ⇒ 超时行自己已经点名了，此时不重复打（``warn_is_redundant``）；
    调用方把上限调高（`--remote-iter-game-timeout` / `--eval-game-timeout-sec`）时，
    这一层就是「跑完但慢」的唯一告警。
  * ``DEFAULT_GAME_TIMEOUT_SEC`` (5.0) —— **首次尝试**的硬顶（调用方没给上限时）：超时 ⇒
    kill + 原地重跑。旧口径「0 = 不限」在本机可以（卡住的是自己的终端），在云机上等于
    「一个卡住的 bun 子进程永远等下去」—— 就是上面那 651s 的成因。
  * ``RETRY_TIMEOUT_FACTOR`` (4.0) —— **重试尝试**的上限倍数（5s → 20s）。为什么重试反而
    放宽：实测单局墙钟是重尾的（本机强并发下 rollout p50 1.8s / p99 16.8s，eval p50 1.2~
    1.6s / p90 3.2~4.2s / p99 7~8s，见 `docs/nn/runtime-opt.md` §8），首次尝试 >5s 已属异常
    （用户口径）值得杀掉重来；但重试是**兜底**——若一次主机抖动把同一局连杀三次，代价是
    「整轮作废重发」或「评估少一局」（读数有偏），比多等十几秒贵得多。调用方**显式**给了
    上限（>0）时对每次尝试一视同仁：配置说了算，不做解释。
  * ``GAME_MAX_ATTEMPTS`` (3) —— 一局最多跑几次：超时 / rc≠0 都**原地重跑同一 argv**
    （种子在 argv 里 ⇒ 同一局是确定性的：重跑要么拿到同一份结果，要么再次响亮失败）。
  * ``GAME_POLL_SEC`` (0.5) —— 轮询粒度：软告警与硬顶都靠它发现（一次 `wait(∞)` 什么都看不见）。
  * ``STALL_WARN_SEC`` (120.0) —— **整轮**停滞线：这么久一局都没结算就点名（带还在飞的局身份）。
    它管的是「所有线程一起卡住、连心跳都哑了」那一档（单局看门狗在那时什么都打不出来）。
  * ``PROGRESS_LOG_SEC`` (60.0) —— 进度行的节流间隔（`progress_due()`）：**按时间**而不是
    按局数，因为这条线的成本只与墙钟有关（用户 2026-09-23：每分钟一句就够）。

为什么重试而不是「竞速副本」（用户 2026-09-22 提的两条路）：argv 不变 ⇒ out 目录不变 ⇒
声明的 shard 集（`data_fp`）逐字节不变；副本会多产一个同 (stage,seed) 的 shard 目录，直接撞上
「实产集 == 声明集」那道门。竞速在**多节点**在线路径上成立是因为那里有 hub 侧的候选表
（`rl/queue_local.py` 的 `pick_race_target` 等）；云机离线只有一个节点，重试是同一效果的最小实现。
"""

from __future__ import annotations

from collections.abc import Sequence

#: 点名线（秒）：单局超过它就打一行 WARN 点名（正常一局亚秒级）。
SLOW_GAME_WARN_SEC = 5.0
#: 首次尝试的硬顶兜底（秒）：调用方没给上限时用它（= 点名线，见模块 docstring）。
DEFAULT_GAME_TIMEOUT_SEC = 5.0
#: 重试尝试的上限倍数（首次 5s ⇒ 重试 20s）；调用方显式给上限时不放大。
RETRY_TIMEOUT_FACTOR = 4.0
#: 一局最多跑几次（首次 + 重试）。
GAME_MAX_ATTEMPTS = 3
#: 轮询粒度（秒）：软告警与硬顶都靠它发现。
GAME_POLL_SEC = 0.5

#: 进度行（「N/M games settled」）的节流间隔（秒）。
#:
#: 用户口径 2026-09-23：云端离线课的日志被进度行刷屏（`[run] kind=iter rollout: 30/224
#: games settled (18s)`）——**每分钟一句就够**。原来的节流是**按局数**（每 10 局一句），
#: 而这条线的成本与局数无关、只与墙钟有关：8 并发下一轮 328 局 3 分钟打完 = 33 行，
#: 而 220 并发的在线节点上是每秒数行。所以改成按**时间**节流（最后一句仍然必打，
#: 否则「跑完了」这件事会没有落点）。
PROGRESS_LOG_SEC = 60.0

#: 「整轮停滞」的告警线（秒）：这么久**一局都没结算**就点名一次，并列出还在飞的局。
#:
#: 为什么需要它（2026-09-25 二次取证「rollout 卡死机器半天」）：进度行与心跳都挂在「有局结算」
#: 上（`progress_due` 在结算主循环里被调用）⇒ 所有线程一起卡住时它们**一起哑**。现场就是
#: 「5s 的 270/336 之后 890s 一行都没有」：机器在卡死，而日志里没有任何一条能说「谁卡住了」。
#: 取 2×`PROGRESS_LOG_SEC`：心跳本来就每分钟一句，这一档只回答「连心跳都停了」。
STALL_WARN_SEC = 120.0


def attempt_timeout_sec(base_sec: float, attempt: int, explicit: bool = False) -> float:
    """第 `attempt` 次尝试的硬顶（秒）。

    `explicit=True`（调用方显式配了上限）⇒ 每次尝试都用它；否则**重试**尝试放宽
    `RETRY_TIMEOUT_FACTOR` 倍（兜底尝试宁可多等，也不因为一次主机抖动丢掉这一局）。
    """
    if explicit or attempt <= 1:
        return float(base_sec)
    return float(base_sec) * RETRY_TIMEOUT_FACTOR


def warn_is_redundant(timeout_sec: float) -> bool:
    """软告警是否与硬顶同值（同值 ⇒ 只打超时行，不再多打一行 WARN）。"""
    return float(timeout_sec) <= SLOW_GAME_WARN_SEC


def progress_due(
    done: int, total: int, now: float, last_at: float, *, every: float | None = None
) -> bool:
    """这一局结算完，该不该打进度行（rollout / eval 两条腿共用一个节流口径）。

    `done >= total`（最后一句）恒 True——收尾那一行是「这一轮结束了」的唯一落点；
    其余按 `now - last_at >= every`（缺省 `PROGRESS_LOG_SEC`）节流。调用方把返回值当
    「现在是不是该打」的判据，并在打完之后把 `last_at` 更新成这次的 `now`。
    """
    if int(done) >= int(total):
        return True
    return (float(now) - float(last_at)) >= (PROGRESS_LOG_SEC if every is None else float(every))


def game_label(stage: object, seed: object) -> str:
    """一局的身份（`s3/d7` 式）。

    诊断行必须能直接说清「是哪一局」——2026-09-22 那 651s 里最缺的就是这个（只有计数）。
    """
    return f"s{stage}/d{seed}"


def slow_warn_line(
    kind: str,
    label: str,
    elapsed: float,
    timeout_sec: float,
    attempt: int,
    where: str,
) -> str:
    """软告警行（**带局身份**；`kind` = `rollout` / `eval`）。"""
    return (
        f"WARN {kind} 单局异常慢：{label} 已 {elapsed:.1f}s"
        f"（正常 <{SLOW_GAME_WARN_SEC:g}s；硬顶 {timeout_sec:g}s；"
        f"第 {attempt}/{GAME_MAX_ATTEMPTS} 次）——现场见 {where}"
    )


def hard_cap_line(kind: str, label: str, elapsed: float, timeout_sec: float, where: str) -> str:
    """硬顶命中行（调用方据此抛可重试失败；`where` 是现场文件）。"""
    return (
        f"{kind} 单局超时（{elapsed:.1f}s > 硬顶 {timeout_sec:g}s）：{label}"
        f"（现场见 {where}）"
    )


def stall_line(kind: str, inflight: int, since_sec: float, labels: Sequence[str]) -> str:
    """整轮停滞行：**已停滞多久 + 还有几局在飞 + 是哪几局**（卡住时唯一能说话的读数）。

    `labels` 按派发顺序给（前 3 个点名，其余只计数）——「谁卡了」正是 2026-09-22 那 651s
    与 2026-09-25 那 890s 里最缺的一条信息。
    """
    who = "、".join(str(x) for x in list(labels)[:3])
    if len(labels) > 3:
        who += "…"
    return (
        f"WARN {kind} 整轮停滞：{since_sec:.0f}s 里一局都没结算（还有 {inflight} 局在飞"
        + (f"：{who}" if who else "")
        + "）——单局看门狗只管单局（5s 硬顶），卡住的往往是**子进程回收或挂载点 IO**，"
        "查那两条（platform_utils.reap_bounded 的那本账）"
    )


def retry_line(
    kind: str,
    label: str,
    attempt: int,
    prev: object,
    timeout_sec: float,
) -> str:
    """重试行：说清「重跑第几次、上次为什么、这次的上限是多少」（上限变化必须可见）。"""
    return (
        f"{kind} 单局重试 {attempt}/{GAME_MAX_ATTEMPTS}：{label}"
        f"（上次：{prev}；本次上限 {timeout_sec:g}s）"
    )


def game_time_summary(kind: str, items: list[tuple[float, str]], *, retried: int = 0) -> str:
    """一轮结束时的**单局耗时分布**（每轮都打：5s 这条线要靠真数据校准，不靠猜）。

    `items` = 逐局 `(墙钟秒, 局身份)`（**成功那次尝试**的墙钟；重试次数用 `retried` 另计）。
    最慢 3 局**点名**——不然「p99=17s」这种读数没法查是哪几局。
    """
    secs = sorted(s for s, _ in items)
    n = len(secs)
    if n == 0:
        return f"{kind} 单局耗时：本轮没有跑成的局（重试过的局 {retried} 个）"
    slowest = sorted(items, key=lambda x: x[0], reverse=True)[:3]
    over = sum(1 for s in secs if s >= SLOW_GAME_WARN_SEC)
    return (
        f"{kind} 单局耗时（{n} 局）：p50={_pct(secs, 0.5):.2f}s p90={_pct(secs, 0.9):.2f}s "
        f"p99={_pct(secs, 0.99):.2f}s max={secs[-1]:.2f}s｜≥{SLOW_GAME_WARN_SEC:g}s 有 {over} 局"
        f"｜重试过的局 {retried} 个｜最慢：" + "、".join(f"{lab}={s:.2f}s" for s, lab in slowest)
    )


def _pct(sorted_secs: list[float], q: float) -> float:
    """有序列表的分位数（最近秩，无插值；列表已排序，调用点负责）。"""
    if not sorted_secs:
        return 0.0
    idx = round(q * (len(sorted_secs) - 1))
    return sorted_secs[min(max(idx, 0), len(sorted_secs) - 1)]


__all__ = [
    "DEFAULT_GAME_TIMEOUT_SEC",
    "GAME_MAX_ATTEMPTS",
    "GAME_POLL_SEC",
    "RETRY_TIMEOUT_FACTOR",
    "SLOW_GAME_WARN_SEC",
    "STALL_WARN_SEC",
    "attempt_timeout_sec",
    "game_label",
    "game_time_summary",
    "hard_cap_line",
    "retry_line",
    "slow_warn_line",
    "stall_line",
    "warn_is_redundant",
]

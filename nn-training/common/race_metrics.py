"""common/race_metrics.py — rollout 竞速账与采集口径（纯函数，stdlib-only）。

从 `common/distribution.py` 下沉（2026-11-11 LOC 预算）：这一组只吃数字与结算行，
不碰网络、不碰权重；`trainer/dispatch` 的 `[dist] round eff` / `dist` 块 / race drops
日志行共用同一处算术——两处各算一遍必然漂（历史上出现过「日志说 38%、事件说 48%」）。

`common.distribution` 保留同名门面（`X as X`），调用点与打桩面一行不改。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def rollout_collect_sec(t_dist_start: float | None, last_settle: float | None) -> float | None:
    """rollout 采集耗时（用户口径 2026-09-19）。

    起点 = **权重就绪开始分发**；终点 = **所有样本采集完毕可交 PPO**（末局结算）。
    边分发边开采下该值**包含**与采集重叠的分发墙钟——这正是用户要的端到端口径。
    """
    if t_dist_start is None or last_settle is None:
        return None
    return round(last_settle - t_dist_start, 1)


def dup_rate(race_total: int, games: int, *, rejected: int = 0) -> float | None:
    """竞速副本的**重复执行率** = 真跑过的重复 / 局数（plan/rollout-corpus-supply §3.4）。

    为什么按局数而不是按任务数：重复执行是「同一局跑了不止一次」，按局数才读得出
    「有多少槽时白烧」（现场 it63：16/42 = 38%）。`games ≤ 0` ⇒ `None`（测不到），
    **不是** 0——0 是「测到零重复」，两者混在一起会让下游误以为那一轮很干净。

    `rejected`（2026-10-21 评审 P1）：`raceDrops` 里 `fanout_rejected` 那一类是**节点按 busy
    直接拒收**的副本——一局都没跑、一个槽秒都没占。把它们算进分子会让「重复执行」虚高
    （现场 503 随处可见），进而把 A2′ 的候选①推成假大头。⇒ 分子 = `race_total - rejected`。
    `rejected > race_total`（调用方口径错）按 0 处理，不返回负数。
    """
    g = int(games)
    if g <= 0:
        return None
    executed = max(0, int(race_total) - max(0, int(rejected)))
    return round(executed / g, 3)


def effective_slots(sum_game_sec: float, collect_sec: float | None) -> float | None:
    """有效并行度 E = Σ(局的服务时长) / 采集墙钟（Little's law；同上 §3.4）。

    * 分子 = **节点服务时长**（`elapsedSec`，不含排队/取任务）——用训练机侧 wall 会把
      取包与网络等待算成「占着槽」，E 虚高；也只累加**结算成功**的那一份
      （竞速副本的 settle 被丢弃、不进这个和）⇒ 它量的是「有用并发」，**下界**；
    * 分母 = `rollout_collect_sec(t_dist_start, last_settle)`，即 **权重就绪（开始分发）→
      末局结算**（`dispatch.py` 的 `[dist] round eff` 传的就是它）——
      ⚠ 不是「首局结算 → 末局结算」：那样会把首波开局的那 1–2 秒服务时长算进分子的同时
      却不计进分母，E 会系统性偏高 ~20%（42 局 9s 墙 / 11s 墙 = 11 vs 9 的差，2026-10-21
      评审 P1）。改这个口径 = 改判据基线（plan §1.1 的 E≈9），三处必须同源。

    `collect_sec` 缺失或 ≤0 ⇒ `None`。返回值保留 1 位小数：E 的用途是「11 vs 16」这类判决，
    不是毫秒级计时。
    """
    if collect_sec is None:
        return None
    wall = float(collect_sec)
    if wall <= 0:
        return None
    return round(max(0.0, float(sum_game_sec)) / wall, 1)


def race_stats(race_drops: Mapping[str, int], games: int) -> tuple[int, int, float | None]:
    """竞速账 → `(真跑过的重复, 503 拒收, 重复执行率)`（**唯一的算术出处**，2026-10-21）。

    日志行与 `dist_block` 都从这里取值：两处各算一遍必然漂（历史上就是「日志说 38%、
    事件说 48%」这类事）。`rejected` = `fanout_rejected`（节点按 busy 直接拒收 ⇒ 零槽秒，
    不算重复执行，见 `dup_rate` 的 docstring）。
    """
    total = sum(int(v) for v in race_drops.values())
    rejected = int(race_drops.get("fanout_rejected", 0) or 0)
    executed = max(0, total - rejected)
    return executed, rejected, dup_rate(total, games, rejected=rejected)


def by_node_of(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """逐节点结算局数（`nodeId` = 下发时的配置 id；缺失回退 agent 自报名）。

    ⚠ 不能用 `summary["node"]` 汇总：那是 agent 自报的 worker 名（`bun-71535` 之类），
    「哪个节点出了几局」在下游会看不见（2026-09-09 现场）。
    """
    out: dict[str, int] = {}
    for s in results:
        nid = str(s.get("nodeId") or s.get("node") or "?")
        out[nid] = out.get(nid, 0) + 1
    return out


def dist_block(
    *,
    iter_id: str,
    by_node: Mapping[str, int],
    nodes: Sequence[Mapping[str, Any]],
    retried: int,
    resumed: int,
    off_plan: int,
    race_drops: Mapping[str, int],
    games: int,
    sum_game_sec: float,
    sum_game_sec_by_node: Mapping[str, float],
) -> dict:
    """一轮收齐后的 `combined["dist"]` 块（`effSlots` 由调用方在算完采集墙钟后补写）。

    口径（plan/rollout-corpus-supply §3.4）：
      * `dupRate` 的分子只算**真跑过**的副本（503 拒收单列 `dupRejected`）；
      * `sumGameSecByNode` 是「远端慢」与「队列先到先得抢走队首」的**唯一判别式**：
        `利用率[n] = sumGameSecByNode[n] / (nodes[n].c × 采集墙钟)`，只给总量时两者分不开。
    """
    executed, rejected, rate = race_stats(race_drops, games)
    return {
        "iterId": iter_id,
        "nodes": dict(by_node),
        "retried": int(retried),
        "resumed": int(resumed),
        "raceDrops": {k: v for k, v in race_drops.items() if v},
        "dupRate": rate,
        "dupExecuted": executed,
        "dupRejected": rejected,
        "slotsOnline": sum(int(n.get("c") or 0) for n in nodes),
        # 3 位小数：stub/小窗用例的单局是 ms 级，1 位会把「累加真的发生了」抹成 0.0。
        "sumGameSec": round(float(sum_game_sec), 3),
        "sumGameSecByNode": {
            k: round(float(v), 3) for k, v in sorted(sum_game_sec_by_node.items()) if v > 0
        },
        # 跨配置断点轮的目录残留量（不在本轮计划、已忽略）——一次性观测
        "offPlanShards": int(off_plan),
    }


def race_log_line(race_drops: Mapping[str, int], games: int, *, sample: int) -> str | None:
    """`[dist] race drops …` 那一行；没有竞速事件 ⇒ `None`（调用方不打）。"""
    executed, rejected, rate = race_stats(race_drops, games)
    if executed + rejected <= 0:
        return None
    kinds = " ".join(f"{k}={v}" for k, v in race_drops.items() if v)
    tail = (
        f"，dup={executed}"
        if rate is None
        else (
            f"，dup={executed}（{rate:.1%} of {games} 局）"
            f"，rejected={rejected}（busy 拒收，未执行）"
        )
    )
    return f"[dist] race drops (expected, sampled≤{sample}/kind): {kinds}{tail}"

"""tests/common/test_rollout_eff.py —— 有效并行度与重复执行的**机读口径**（plan/rollout-corpus-supply §3.4）。

为什么要有独立的纯函数（而不是在 `[dist]` 轮末里现算）：这两个数现在是 A2′ 的**决策输入**
——「竞速副本重复执行」与「远端槽忙碌率」谁是大头，直接决定下一刀落在哪。口径写在日志里
却算在脑子里，就是下一次误读（v2 把「自机 69%」读成「本地闸不公」，其实是满产能）。

  * `dup_rate(race_total, games, rejected=…)`：重复执行率。**分母是局数**（不是任务数）：重复执行是
    「同一局跑了不止一次」，按局数才读得出「有多少槽时白烧」；**分子只算真跑过的**
    （503 busy 被拒的副本零槽秒，单列 `rejected`，2026-10-21 评审 P1）；
  * `effective_slots(sum_game_sec, collect_sec)`：Little's law 的 E = Σ服务时长 / 采集墙钟。
    **分母 = `rollout_collect_sec`（权重就绪 → 末局结算）**，不是整轮 wall（含发布/落盘 ⇒ 稀释），
    也不是「首局结算 → 末局结算」（首波开局那 1–2s 只进分子不进分母 ⇒ E 虚高 ~20%）。

两者都返回 `None` 而不是 0：0 是「测到 0」，None 是「测不到」（缺时间戳/零局），
混成一个值会让「哪一轮数据不可用」在下游消失。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.distribution import (
    by_node_of,
    dist_block,
    dup_rate,
    effective_slots,
    race_log_line,
    race_stats,
)


def test_dup_rate_per_game() -> None:
    # 现场 it63：fanout_settled=9 + fanout_inflight=7 ⇒ 16 次重复执行 / 42 局
    assert dup_rate(16, 42) == 0.381
    assert dup_rate(0, 42) == 0.0
    assert dup_rate(1, 3) == 0.333


def test_dup_rate_excludes_busy_rejects() -> None:
    """★ 503 busy 被拒的副本零槽秒，不许算进重复执行（2026-10-21 评审 P1）。

    现场同一行日志里既有 `fanout copy … failed (HTTP 503: busy)`（never ran）
    也有真的跑完但输了的副本——混在一起会把 38% 读成「白烧了 38% 槽时」。
    """
    assert dup_rate(16, 42, rejected=7) == 0.214  # (16-7)/42
    assert dup_rate(7, 42, rejected=7) == 0.0  # 全是拒收 ⇒ 零重复执行
    assert dup_rate(3, 42, rejected=99) == 0.0  # 调用方口径错 ⇒ 钳到 0，不给负数


def test_dup_rate_without_games_is_none() -> None:
    assert dup_rate(0, 0) is None
    assert dup_rate(5, 0) is None


def test_effective_slots_is_little_law() -> None:
    # 42 局 × 2.4s = 100.8 槽秒 / 9s = 11.2 槽
    assert effective_slots(100.8, 9.0) == 11.2
    assert effective_slots(0.0, 9.0) == 0.0


def test_effective_slots_without_wall_is_none() -> None:
    assert effective_slots(10.0, 0.0) is None
    assert effective_slots(10.0, None) is None


# ─────────────────── 轮末读数的**组装**（同一族：`common/distribution.py`）───────────────────
#: 现场 it63 拆开后的形状：16 次事件里 7 次是 503 busy 拒收（与 `fanout_inflight` **互斥**——
#: 代码里是 elif，同一次失败只落一个桶），真跑过的 = 9 settled + 4 main_by_fanout。
_RACE = {"fanout_settled": 9, "fanout_rejected": 7, "main_by_fanout": 4}


def test_race_stats_is_the_single_source_of_truth() -> None:
    """`(真跑过, 拒收, 率)` 只有一处算术；日志与事件都从它取值（两处各算一遍必漂）。"""
    assert race_stats(_RACE, 42) == (13, 7, round(13 / 42, 3))
    assert race_stats({"fanout_rejected": 3}, 42) == (0, 3, 0.0)
    assert race_stats({}, 0) == (0, 0, None)  # 零局 ⇒ 测不到（不是 0）


def test_by_node_of_prefers_config_id() -> None:
    rows = [
        {"nodeId": "self", "node": "bun-71535"},
        {"nodeId": "self", "node": "bun-71535"},
        {"node": "bun-9"},  # 旧结果/本地直跑：没有 nodeId ⇒ 回退自报名
    ]
    assert by_node_of(rows) == {"self": 2, "bun-9": 1}


def test_dist_block_splits_rejects_from_dups() -> None:
    block = dist_block(
        iter_id="run.63",
        by_node={"self": 29, "mac": 6},
        nodes=[{"id": "self", "c": 8}, {"id": "mac", "c": 2}],
        retried=2,
        resumed=0,
        off_plan=3,
        race_drops=_RACE,
        games=42,
        sum_game_sec=100.8,
        sum_game_sec_by_node={"self": 69.6, "mac": 31.2},
    )
    assert block["slotsOnline"] == 10  # 分母来自 nodes[].c，不是局数
    assert (block["dupExecuted"], block["dupRejected"]) == (13, 7)
    assert block["dupRate"] == round(13 / 42, 3)  # 不是 20/42
    assert block["sumGameSecByNode"] == {"mac": 31.2, "self": 69.6}
    assert block["offPlanShards"] == 3
    # `effSlots` 要等采集墙钟（`rollout_collect_sec`），调用方补写——这里断言它缺席
    assert block.get("effSlots") is None


def test_race_log_line_is_silent_without_events() -> None:
    assert race_log_line({}, 42, sample=2) is None
    assert race_log_line({"fanout_settled": 0}, 42, sample=2) is None
    line = race_log_line(_RACE, 42, sample=2)
    assert line is not None and line.startswith("[dist] race drops")

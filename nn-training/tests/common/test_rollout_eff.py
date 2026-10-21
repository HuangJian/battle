"""tests/common/test_rollout_eff.py —— 有效并行度与重复执行的**机读口径**（plan/rollout-corpus-supply §3.4）。

为什么要有独立的纯函数（而不是在 `[dist]` 轮末里现算）：这两个数现在是 A2′ 的**决策输入**
——「竞速副本重复执行」与「远端槽忙碌率」谁是大头，直接决定下一刀落在哪。口径写在日志里
却算在脑子里，就是下一次误读（v2 把「自机 69%」读成「本地闸不公」，其实是满产能）。

  * `dup_rate(race_total, games)`：重复执行率。**分母是局数**（不是任务数）：重复执行是
    「同一局跑了不止一次」，按局数才读得出「有多少槽时白烧」；
  * `effective_slots(sum_game_sec, collect_sec)`：Little's law 的 E = Σ服务时长 / 采集墙钟。
    **分母是采集墙钟**（首局结算 → 末局结算），不是整轮 wall —— 后者含发布/落盘，会把 E 稀释。

两者都返回 `None` 而不是 0：0 是「测到 0」，None 是「测不到」（缺时间戳/零局），
混成一个值会让「哪一轮数据不可用」在下游消失。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.distribution import dup_rate, effective_slots


def test_dup_rate_per_game() -> None:
    # 现场 it63：fanout_settled=9 + fanout_inflight=7 ⇒ 16 次重复执行 / 42 局
    assert dup_rate(16, 42) == 0.381
    assert dup_rate(0, 42) == 0.0
    assert dup_rate(1, 3) == 0.333


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

"""tests/worker/test_stream_collect_stats.py —— 云机侧的**分关采集账**（`stage_stats`）。

plan/it-stage-ratation.plan.md §9.5（2026-10-28）：云机轮此前只把 `volumeTopup` 放在
`iter_info` 里（形状与本机腿不同、也没进行账），于是控制台的「各关进度」列/面板在云机段
恒为 `—`，而本机段有读数 —— 同一条曲线两条腿不可比。

这里钉两件事：

  ① **同形**：`stage_stats()` 的键集/取值口径与 `trainer/loop_volume._volume_stage_stats`
     一致（读面 `dashboard/src/server/iters.ts::parseIterVolume` 只认这一个形状）；
  ② **真数**：`collected`/`games`/`est_s`/`cap_games`/`shortfall`/`halted` 都是采集器账本
     本身，不是另算一份（同源）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers.stream_spec import STAGES, TARGET, build_spec
from worker.stream_collect import StreamCollector
from worker.volume_alloc import VOLUME_ALLOC_RULE
from worker.volume_quota import target_per_stage


def _collector() -> StreamCollector:
    col = StreamCollector.from_spec(build_spec())
    assert col is not None
    return col


def test_stage_stats_is_local_leg_shaped() -> None:
    """★ 形状与 `_volume_stage_stats` 同规：顶层 rule/quota/halted/stages/shortfall，逐关四格。"""
    col = _collector()
    stats = col.stage_stats()
    assert stats["rule"] == VOLUME_ALLOC_RULE
    assert stats["quota"] == target_per_stage(TARGET, len(STAGES))
    assert stats["est_src"] == "plan"  # 云机的 est 是导出时钉进计划的（节点没有 hub jsonl）
    assert set(stats["stages"]) == {str(s) for s in STAGES}
    for stage in STAGES:
        cell = stats["stages"][str(stage)]
        assert set(cell) == {"collected", "games", "est_s", "cap_games"}
        assert cell["collected"] == 0
        assert cell["games"] == 0
        assert cell["est_s"] == col.ests[stage]
        assert cell["cap_games"] == col._plan_caps[stage]
    # 一局都没采 ⇒ 每关都缺、halted=False
    assert stats["halted"] is False
    assert set(stats["shortfall"]) == {str(s) for s in STAGES}


def test_stage_stats_follows_settled_games() -> None:
    """结算一局之后：collected/games/shortfall 跟着走；满额 ⇒ halted=True。"""
    col = _collector()
    quota = target_per_stage(TARGET, len(STAGES))
    # 每关派一局并结算（样本量按计划 est 给）
    for stage in STAGES:
        argv = None
        while True:
            nxt = col.next_game()
            assert nxt is not None
            if _stage_of(nxt) == stage:
                argv = nxt
                break
        col.observe(argv, col.ests[stage])
    stats = col.stage_stats()
    assert all(stats["stages"][str(s)]["games"] >= 1 for s in STAGES)
    assert all(stats["stages"][str(s)]["collected"] > 0 for s in STAGES)
    assert stats["halted"] is False
    # 逐关补到满额：collected ≥ quota ⇒ 该关从 shortfall 里消失
    for stage in STAGES:
        while stats["stages"][str(stage)]["collected"] < quota:
            nxt = col.next_game()
            assert nxt is not None
            col.observe(nxt, quota)  # 一局补满（读数面用例，不追真实样本量）
            stats = col.stage_stats()
    assert stats["shortfall"] == {}
    assert stats["halted"] is True
    assert all(stats["stages"][str(s)]["collected"] >= quota for s in STAGES)


def test_stage_stats_matches_volume_topup_numbers() -> None:
    """与 `volume()` 同一份账本：collected/games 逐关相同（不另算一份）。"""
    col = _collector()
    for _ in range(3):
        nxt = col.next_game()
        assert nxt is not None
        col.observe(nxt, 200)
    stats = col.stage_stats()
    topup = col.volume()
    assert {int(k): v["collected"] for k, v in stats["stages"].items()} == topup["collected_by_stage"]
    assert {int(k): v["games"] for k, v in stats["stages"].items()} == topup["games_by_stage"]


def _stage_of(argv: list[str]) -> int:
    return int(argv[argv.index("--stages") + 1])

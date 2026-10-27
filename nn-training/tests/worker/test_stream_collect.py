"""tests/worker/test_stream_collect.py — 逐局流式采集调度器（2026-10-27 用户口径）。

钉住的就是用户那句话的三段：

  ① **采完一局就判差额最大的关**：`observe` 之后下一局必须给最缺的关（不是按 stage 升序、
     也不是按「这一批还剩几局」）；
  ② **用空出来的 worker 采它**：没有活可派时返回**空列表**（供给不关，下一局结算再问），
     只有**所有关满额/触顶**才返回 `None`（终局）——两者混了就是静默短采；
  ③ **直到所有关卡样本量满额**：逐关 `collected ≥ quota` 才停；硬顶兜「零样本关」，触顶
     必须响（`capped_stages`）。

另外钉两条静默失败形态：

  * **同一条连续流**：每关第 k 局 = `stage_seeds(…, start=k, n=1)`——不重号、不漏号（重启/
    重派都不会换签）；
  * **同关模板**：`--stage-json` 只从本关模板取（跨关取模板会把自定义关的 JSON 删掉/带错）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from tests.helpers.stream_spec import (
    ESTS,
    IT,
    ROTATE,
    STAGES,
    TARGET,
    build_spec,
    seed_of,
    stage_of,
    template,
    volume_block,
)
from worker.stream_collect import StreamCollector
from worker.volume_alloc import DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN
from worker.volume_quota import stage_seeds, target_per_stage

_spec = build_spec
_volume = volume_block
_template = template
_stage_of = stage_of
_seed_of = seed_of


def _collector(spec: dict | None = None, **kw: object) -> StreamCollector:
    col = StreamCollector.from_spec(spec or _spec(), **kw)  # type: ignore[arg-type]
    assert col is not None
    return col


# ────────────────────────── ① 差额最大的关先派 ──────────────────────────


def test_next_game_gives_the_starved_stage_the_freed_slot() -> None:
    """三关各派一局、结算后：只出了 10 样本的那一关最缺 ⇒ **下一局必须给它**。

    这条是用户口径的字面量：判据是**已结算样本**的差额，不是 stage 序号、也不是批次剩余数。
    旧形态（`spread_games_by_stage` 取 `eligible[:width]`）在这里会派回 2000（序号最小），
    正是现场「前 9 批一直在补 2000-2003」的来路。
    """
    col = _collector()
    first = [col.next_game() for _ in range(len(STAGES))]
    assert [a and _stage_of(a) for a in first] == STAGES, "冷启动先给每关一局（不猜，但要有测量）"
    col.observe(first[0] or [], 100)
    col.observe(first[1] or [], 10)  # 2001 是短局关
    col.observe(first[2] or [], 100)
    got = col.next_game()
    assert got, "还有缺口，必须继续派"
    assert _stage_of(got) == 2001


def test_a_short_game_stage_keeps_receiving_until_it_catches_up() -> None:
    """差 290 vs 差 200 ⇒ 连续几局都该给 2001（每局只补 25 个样本）。"""
    col = _collector()
    first = [col.next_game() for _ in range(len(STAGES))]
    for argv, n in zip(first, [100, 10, 100], strict=True):
        col.observe(argv or [], n)
    order = [_stage_of(col.next_game() or []) for _ in range(3)]
    assert order == [2001, 2001, 2001], f"短局关没被连续补：{order}"


def test_cold_start_returns_empty_while_one_game_per_stage_is_in_flight() -> None:
    """没有局均估计（计划 est 全 0）时：每关先只留**一局**在飞。

    这时 `next_game()` 必须返回**空列表**（本拍没活，但供给不关）——返回 `None` 会让调用方
    关掉供给 ⇒ 只采到「关数」局就收工，静默短采。唯一的 `None` 出现在所有关满额/触顶时。
    """
    col = _collector(_spec(vol=_volume(ests={s: 0 for s in STAGES})))
    assert [col.next_game() for _ in range(len(STAGES))] != []
    assert col.next_game() == [], "每关都有一局在飞 ⇒ 本拍无活，但绝不是终局"
    col.observe([], 0)  # 不存在的局：账本不动（防呆）
    assert col.next_game() == []


# ────────────────────────── ② 满额即停 / 硬顶 ──────────────────────────


def test_stops_exactly_when_every_stage_reaches_quota() -> None:
    """逐局结算 100 样本/关、quota 300 ⇒ **每关恰好 3 局**就 `None`（不多派一局）。"""
    col = _collector(_spec(vol=_volume(ests={s: 0 for s in STAGES})))
    quota = target_per_stage(TARGET, len(STAGES))
    per_stage = {s: 0 for s in STAGES}
    for _ in range(3 * len(STAGES)):
        argv = col.next_game()
        assert argv, "还没满额就断供 = 静默短采"
        assert argv
        stage = _stage_of(argv)
        per_stage[stage] += 1
        col.observe(argv, 100)
    assert per_stage == {s: 3 for s in STAGES}
    assert all(v >= quota for v in col.collected.values())
    assert col.next_game() is None, "所有关满额 ⇒ 终局（不再派）"


def test_zero_sample_stage_hits_the_cap_and_is_reported_loudly() -> None:
    """一关永远 0 样本：撞硬顶后**终局**，且 `capped_stages` / `shortfall_by_stage` 里可见。

    这是「静默短采」的反面：短采可以发生（机器/关卡坏了），但账上必须说得清。
    """
    col = _collector()
    first = [col.next_game() for _ in range(len(STAGES))]
    for argv in first:
        assert argv
        col.observe(argv, 0 if _stage_of(argv) == 2001 else 300)
    for _ in range(200):
        argv = col.next_game()
        if argv is None:
            break
        if not argv:
            pytest.fail("所有关都已结算，不该出现「无活可派」")
        col.observe(argv, 0 if _stage_of(argv) == 2001 else 300)
    assert col.next_game() is None
    vol = col.volume()
    assert vol["capped_stages"] == [2001]
    assert 2001 in vol["shortfall_by_stage"]
    assert vol["collected_by_stage"][2001] == 0
    # 硬顶 = 4× 缺口局数（计划 est 100 ⇒ ceil(300/100)=3 ⇒ 12），不是无限循环。
    assert vol["games_by_stage"][2001] <= 4 * 3


def test_explicit_game_cap_is_respected() -> None:
    """`topup.max_games_per_stage` 是**硬顶**：设 2 局就只派 2 局（哪怕配额没满）。"""
    col = _collector(
        _spec(vol=_volume(ests={s: 0 for s in STAGES}, topup={"max_games_per_stage": 2}))
    )
    seen: list[int] = []
    while True:
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        seen.append(_stage_of(argv))
        col.observe(argv, 0)
    assert sorted(set(seen)) == STAGES
    assert len(seen) == 2 * len(STAGES), f"每关应恰好 2 局（硬顶）：{seen}"
    assert col.volume()["capped_stages"] == STAGES


# ────────────────────────── ③ 连续流 / 模板 ──────────────────────────


def test_seeds_continue_the_per_stage_stream_without_gaps_or_dupes() -> None:
    """每关派出去的 seed 必须是该关连续流的**前缀** `[0, k)`（不重号、不漏号）。"""
    col = _collector(_spec(vol=_volume(ests={s: 0 for s in STAGES})))
    got: dict[int, list[int]] = {s: [] for s in STAGES}
    while True:
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        got[_stage_of(argv)].append(_seed_of(argv))
        col.observe(argv, 100)
    for stage in STAGES:
        want = stage_seeds(ROTATE, IT, stage, 0, len(got[stage]))
        assert got[stage] == want, f"stage {stage} 的 seed 流断了：{got[stage]} != {want}"
        assert len(set(got[stage])) == len(got[stage])


def test_custom_stage_json_comes_from_that_stage_only() -> None:
    """`--stage-json` 只从**本关**模板取（跨关取模板会把它删掉/带错——自定义关课程）。"""
    json_2001 = '{"board":[[1,2],[3,4]]}'
    spec = _spec(stage_json={2001: json_2001})
    col = _collector(spec)
    seen: dict[int, list[str]] = {s: [] for s in STAGES}
    while True:
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        stage = _stage_of(argv)
        seen[stage].append(argv[argv.index("--stage-json") + 1] if "--stage-json" in argv else "")
        col.observe(argv, 100)
    assert seen[2001] and all(v == json_2001 for v in seen[2001])
    assert seen[2000] == [""] * len(seen[2000]) and seen[2002] == [""] * len(seen[2002])


def test_out_names_never_collide_with_the_planned_argv() -> None:
    """`--out` 全局续号：动态局的 `w{i}` 不与计划里任何一条重号（重号 = 覆盖别人的产出）。"""
    spec = _spec()
    planned = {argv[argv.index("--out") + 1] for argv in spec["argv"]}
    col = _collector(spec)
    outs: list[str] = []
    while True:
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        outs.append(argv[argv.index("--out") + 1])
        col.observe(argv, 100)
    assert outs and not (set(outs) & planned), f"动态局与计划局重号：{sorted(set(outs) & planned)}"
    assert len(set(outs)) == len(outs)


# ────────────────────────── ④ 接线面 ──────────────────────────


def test_absent_or_disabled_volume_means_no_stream() -> None:
    """`volume` 缺席（在线 kind=iter / 老计划）或显式关闭 ⇒ 不开流（老行为逐字不变）。"""
    assert StreamCollector.from_spec({"argv": [_template()]}) is None
    off = _volume(topup={"enabled": False})
    assert StreamCollector.from_spec(_spec(vol=off)) is None
    zero_batches = _volume(topup={"max_batches": 0})
    assert StreamCollector.from_spec(_spec(vol=zero_batches)) is None


def test_illegal_volume_is_loud_not_silent() -> None:
    """畸形 volume 块 ⇒ `ProtocolError`（绝不静默降级成「不采」）。"""
    bad = _volume()
    bad["per_stage_quota"] = int(bad["per_stage_quota"]) + 1  # 与 ceil(target/关数) 不同源
    with pytest.raises(ProtocolError):
        StreamCollector.from_spec(_spec(vol=bad))


def test_volume_block_reports_stream_mode_and_planned_games() -> None:
    """轮报 `volumeTopup` 的读数面：`mode=per-game-stream` + 计划局数 + 逐关账。"""
    spec = _spec()
    col = _collector(spec)
    while (argv := col.next_game()) is not None:
        assert argv
        col.observe(argv, 100)
    vol = col.volume()
    assert vol["mode"] == "per-game-stream"
    assert vol["planned_games"] == len(spec["argv"])
    assert vol["planned_by_stage"] == {s: 3 for s in STAGES}
    assert vol["shortfall_by_stage"] == {}
    assert vol["wasted_samples"] == 0
    assert vol["dispatched_games"] == sum(vol["games_by_stage"].values())
    assert max(vol["games_by_stage"].values()) <= DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN

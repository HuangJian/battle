"""e2e/test_stream_collect_e2e.py — **自主云机 rollout 的逐局流式采集**（2026-10-27 用户口径）。

用户原话（第 N 次重申，所以这次钉成集成用例）：

> 采完一局，立即判断差额最大的关卡，立即用空出来的 worker 采集该差额关卡的样本，
> 直到所有关卡样本量满额。不需要一轮一轮的补。

链路（除了「跑一局」那一步，全部是真代码）：

    worker.stream_collect.StreamCollector          # 调度：谁最缺 ⇒ 下一局给它什么 argv
      ↓ next_game() / observe(argv, nSamples)
    worker.iter_rollout.run_iter_rollout           # 真调度循环 / 真 verify_shards /
      ├─ 假 `_run_one_game_bounded`（不起 bun、不跑 sim）  #   真 collect_reports / 真聚合
      │    └─ 按 argv 写 `w{i}/rl_sX_seedY/manifest.json` + `_rl_report.json`
      ├─ 真 `verify_shards`（实产集 == **实际派出去**的声明集）
      └─ 真 `report["perGame"] / perGameSecs / shards`

两条用例各钉一半：

  ① `test_a_freed_slot_is_refilled_before_the_other_game_lands`：**槽位空出即补**。
     慢局还被夹具按着的时候，快局结算出来的槽位已经派了下一局 —— 旧「一批跑完再算
     下一批」的形态在这一条上必红（它只能等两局都收齐）。慢局**不睡固定时长**：
     放行条件是「第三局已开跑」这个**事件**（上限 `HOLD_MAX_SEC` 只兜退化形态，
     不让套件挂住）——按墙钟余量写的话，−n 8 上时间片一抖就是 flake。
  ② `test_round_mints_per_game_until_every_stage_is_full`：**一局一拍直到满额**。
     单槽位 + 每关固定样本量 ⇒ 每关恰好 `quota / samples_per_game` 局（一个不多、一个
     不少），顺序是「每关先各一局、之后差额最大者优先」，seed 是该关连续流的前缀。

为什么不打桩 `run_iter_rollout`：那正是历次改坏的层（调度循环仍是「提交一批 → 等齐 →
再提交下一批」），桩掉它就等于只测了 `StreamCollector` 本身（那在
`tests/worker/test_stream_collect.py` 已钉死）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.iter_rollout as iter_rollout
from tests.helpers.stream_spec import (
    IT,
    ROTATE,
    STAGES,
    WVER,
    build_spec,
    seed_of,
    stage_of,
    volume_block,
)
from worker.volume_quota import stage_seeds

#: 夹具「按住」一局的上限（见 ①）：放行的正路是「第 N 局已开跑」这个**事件**；这个上限只
#: 保证退化形态（批式调度）也能自己收尾、由断言判红，而不是把套件挂住。
HOLD_MAX_SEC = 15.0


class _Trace:
    """一局的开跑/收工事件 —— **「谁先谁后」只许用事件序号判**（2026-10-27 修）。

    ★ 为什么不能用时间戳：`time.monotonic()` 在系统上是**有颗粒度**的（Windows 实测
    15.6ms 一档），而本文件的两条事件是同一条因果链上的**相邻**事件 —— 第三局开跑
    ⇒ `release.set()` ⇒ 被按住的慢局醒来收工。它们之间的真实间隔常常只有几十微秒 ⇒
    两次采样落在**同一个刻度** ⇒ `start3 < end_slow` 因为**相等**而红（全量并发下实测
    `assert 655668.75 < 655668.75`）。

    ★ 为什么序号是可靠的：慢局的 `end` **只能**发生在 `release.set()` 之后，而 set 与
    「第 N 次 start 的登记」同属一个临界区 ⇒ `events` 里第三局 start 的下标**严格小于**
    慢局 end 的下标。这条由因果保证，与墙钟、颗粒度、机器负载全部无关。

    ★ 顺带：`n_starts` 原来是裸 `+=`（多线程重串口计数会丢一次 ⇒ 要等第 4 局才放行，
    而第 4 局可能永远不来 ⇒ 慢局撑到 `HOLD_MAX_SEC` 超时 ⇒ 用例偶发红）。现在与
    events 的 append 共用一把锁。
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, int, int, float]] = []
        self.samples: dict[int, int] = {}
        self.sleep: dict[int, float] = {}
        self.zero_sample_stages: set[int] = set()
        #: 被「按住」的关：这些关的局不睡固定时长，而是等 `release`（判**调度顺序**，
        #: 不是判墙钟余量 —— 见 ①）。
        self.held_stages: set[int] = set()
        #: 第几局开跑就放行被按住的局（`None` = 从不主动放行，只走 `HOLD_MAX_SEC` 兜底）。
        self.release_after_starts: int | None = None
        self.release = threading.Event()
        self.n_starts = 0
        #: 「按满了 HOLD_MAX_SEC 才结算」的局：这就是「槽位没被及时补上」的直接证据。
        self.hold_timeouts: list[str] = []
        self._lock = threading.Lock()

    def _append(self, kind: str, stage: int, seed: int) -> int:
        """登记一条事件，返回「这是第几次 start」（0 = 这是一条 end）。"""
        with self._lock:
            self.events.append((kind, int(stage), int(seed), time.monotonic()))
            if kind != "start":
                return 0
            self.n_starts += 1
            n = self.n_starts
        # ★ set 在**锁外**：它是「这一局的 start 已完整登记」之后的事；放在锁内会让
        #   慢局线程醒来时可能挤在同一个时间片里抢锁（不改变正确性，但扰动时序）。
        if self.release_after_starts is not None and n >= self.release_after_starts:
            self.release.set()
        return n

    def start(self, stage: int, seed: int) -> None:
        self._append("start", stage, seed)

    def end(self, stage: int, seed: int) -> None:
        self._append("end", stage, seed)

    def start_index(self, nth: int) -> int:
        """第 `nth` 次开跑（1 起）在 `events` 里的下标 —— 「谁先谁后」的唯一可靠判据。"""
        hits = [i for i, e in enumerate(self.events) if e[0] == "start"]
        assert len(hits) >= nth, f"只开跑了 {len(hits)} 局，取不到第 {nth} 次：{_fmt(self.events)}"
        return hits[nth - 1]

    def first_end_index(self, stage: int) -> int:
        """该关第一次收工在 `events` 里的下标（`AssertionError` = 这一关一局都没收工）。"""
        for i, e in enumerate(self.events):
            if e[0] == "end" and e[1] == stage:
                return i
        raise AssertionError(f"stage {stage} 一局都没收工：{_fmt(self.events)}")

    def timeline(self) -> str:
        """诊断面：`0:start@2000/seed… 1:end@2001/seed…`（序号 + 事件 + 关/seed）。"""
        return _fmt(self.events)

    def starts(self) -> list[tuple[int, int, float]]:
        return [(s, d, t) for kind, s, d, t in self.events if kind == "start"]

    def ends(self) -> list[tuple[int, int, float]]:
        return [(s, d, t) for kind, s, d, t in self.events if kind == "end"]


def _fmt(events: list[tuple[str, int, int, float]]) -> str:
    return " | ".join(f"{i}:{kind}@{s}/{d}" for i, (kind, s, d, _t) in enumerate(events))


def _fake_game(trace: _Trace) -> Any:
    """`_run_one_game_bounded` 的替身：按 argv 写导出器同形产物，样本数由 trace 指定。"""

    def _run(
        bun: str,
        argv: list[str],
        exec_dir: Path,
        ts_dir: Path,
        out_dir: str,
        timeout_sec: float,
        log: Any = None,
        explicit: bool = False,
        pool: Any = None,
        *,
        ceiling_sec: float | None = None,
        job_dir: Path | None = None,
    ) -> tuple[float, int]:
        stage = stage_of(argv)
        seed = seed_of(argv)
        trace.start(stage, seed)
        delay = float(trace.sleep.get(stage, 0.0))
        if stage in trace.held_stages:
            # 这一局被夹具按住，等「第 N 局已开跑」的放行事件（有界，见 ①）。按满了才
            # 结算 ⇒ 记一笔（判据在 ①：那是「空出的槽位没被补上」的直接证据）。
            if not trace.release.wait(HOLD_MAX_SEC):  # sleep-ok: 等的是事件（哪一局先开跑），不是墙钟时长
                trace.hold_timeouts.append(f"stage{stage}/seed{seed}")
        elif delay > 0:
            time.sleep(delay)  # sleep-ok: 夹具模拟的工作量：这一关一局本身的耗时（慢局关 vs 快局关）
        out = Path(exec_dir) / out_dir
        n = 0 if stage in trace.zero_sample_stages else int(trace.samples[stage])
        shard = out / f"rl_s{stage}_seed{seed}"
        shard.mkdir(parents=True, exist_ok=True)
        (shard / "manifest.json").write_text(
            json.dumps(
                {
                    "stage": stage,
                    "seed": seed,
                    "nSamples": n,
                    "ticks": max(1, n),
                    "outcome": "stage_clear",
                    "wver": WVER,
                    "course_fp": "cfp-e2e",
                    "corpus_fp": "xfp-e2e",
                }
            ),
            encoding="utf-8",
        )
        (out / "_rl_report.json").write_text(
            json.dumps(
                {
                    "games": 1,
                    "outcomes": {"stage_clear": 1},
                    "totalSamples": n,
                    "totalTicks": max(1, n),
                    "scoreList": [0.5],
                    "dimLists": {"kills": [1.0]},
                }
            ),
            encoding="utf-8",
        )
        trace.end(stage, seed)
        return delay, 1

    return _run


@pytest.fixture
def hermetic_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点侧「不起 bun / 不换根 / 不建池」的三处打桩（e2e 层的既有口径）。"""
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": "bun-stub")
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "stub")
    monkeypatch.setattr(iter_rollout, "_make_pool", lambda *a, **k: None)
    # 落点：一律走「回退原地」（产出住真 job 目录）——本用例要断言的就是目录里的实产。
    monkeypatch.setattr(iter_rollout.scratch, "open_scratch", lambda *a, **k: None)


def _run_round(
    tmp_path: Path,
    trace: _Trace,
    *,
    vol: dict,
    workers: int,
) -> tuple[dict, list[str], Any]:
    job = tmp_path / "job"
    job.mkdir()
    job.joinpath("init_weights.json").write_text("{}", encoding="utf-8")
    spec = build_spec(vol=vol, workers=workers)
    from worker.stream_collect import StreamCollector

    collector = StreamCollector.from_spec(spec, log=lambda m: None)
    assert collector is not None, "volume 块在 ⇒ 必须开流"

    logs: list[str] = []
    info = iter_rollout.run_iter_rollout(
        job,
        spec,
        ts_dir=job,
        log=logs.append,
        it=IT,
        next_game=collector.next_game,
        on_game_settled=collector.observe,
    )
    return info, logs, collector


# ────────────────────────── ① 槽位空出即补 ──────────────────────────


def test_a_freed_slot_is_refilled_before_the_other_game_lands(
    tmp_path: Path, hermetic_node: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """慢局还在飞时，快局交出的槽位必须**立刻**派下一局（不是等整批发完）。

    设计：两关、两槽位。2000 一局就够配额（快局），2001 要三局（慢局）。旧形态
    （提交一批 → 等齐 → 再补一批）在**慢局结算之前**不可能有第三局开跑；流式形态下第三局
    在快局交工（t≈0.005s）就开跑（2000 已满额 ⇒ 下一局只能给 2001）。

    ★ 慢局不是「固定睡 0.5s」而是**被夹具按住**：放行的条件是「第 3 局已经开跑」这个事件，
    按满了 `HOLD_MAX_SEC` 才结算就直接判红（`hold_timeouts` 非空 = 空出的槽位没被及时补上）。

    ★★ 「第三局早于慢局结算」这条判**只能看事件序号**，不能看时间戳（2026-10-27 修）：
    放行是第三局开跑的**直接后果**，两者的真实间隔常常 < `time.monotonic()` 的一档
    （Windows 15.6ms）⇒ `t_start3 < t_slow_end` 会因**相等**而偶发红（全量并发下实测两次）。
    序号不存在这个问题：慢局的 `end` 只能登记在 `release.set()` 之后，而 set 与「第 3 次
    start 的登记」同属一个临界区 ⇒ 下标严格有序（因果保证，与负载无关）。
    """
    stages = [2000, 2001]
    vol = volume_block(stages=stages, ests={2000: 100, 2001: 100}, target=600)
    trace = _Trace()
    trace.samples = {2000: 300, 2001: 100}
    trace.sleep = {2000: 0.005}
    trace.held_stages = {2001}
    trace.release_after_starts = 3
    monkeypatch.setattr(iter_rollout, "_run_one_game_bounded", _fake_game(trace))
    info, _logs, collector = _run_round(tmp_path, trace, vol=vol, workers=2)

    starts = trace.starts()
    assert not trace.hold_timeouts, (
        f"慢局被按满 {HOLD_MAX_SEC:g}s 才结算：第三局没在慢局结算前开跑 —— 这不是流式"
        f"（槽位空出就得补）：{trace.hold_timeouts}｜事件序列 {trace.timeline()}"
    )
    assert len(starts) >= 3, f"应当派了 3 局以上（2000×1 + 2001×3）：{trace.timeline()}"
    # 事件序列上的先后（见上面的 ★★）：第三局必须登在「慢局第一次收工」**之前**。
    idx_third_start = trace.start_index(3)
    idx_slow_end = trace.first_end_index(2001)
    assert idx_third_start < idx_slow_end, (
        "第三局是在慢局结算**之后**才开跑的 —— 这不是流式（槽位空出就该补）"
        f"｜事件序列 {trace.timeline()}"
    )
    assert collector.next_game() is None
    assert info["report"]["shards"] == len(starts)


# ────────────────────────── ② 一局一拍直到满额 ──────────────────────────


def test_round_mints_per_game_until_every_stage_is_full(
    tmp_path: Path, hermetic_node: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单槽位：逐局结算 ⇒ 每关恰好 `quota / 每局样本` 局；seed 是连续流的前缀。

    quota = 300；每局样本 = {2000: 100, 2001: 25, 2002: 50} ⇒ 局数 = {3, 12, 6}，合计 21。
    计划预排只有 9 局（每关 3 局）——旧形态会**停在 9 局**（再补几批），新形态按实测补齐。
    """
    vol = volume_block(ests={s: 0 for s in STAGES})  # 冷启动：计划里没有局均估计
    spec = build_spec(vol=vol, workers=1)
    assert len(spec["argv"]) == 3, "计划预排：est=0 ⇒ 每关 1 局（不猜）"

    trace = _Trace()
    trace.samples = {2000: 100, 2001: 25, 2002: 50}
    monkeypatch.setattr(iter_rollout, "_run_one_game_bounded", _fake_game(trace))
    info, _logs, collector = _run_round(tmp_path, trace, vol=vol, workers=1)

    dispatched = [(s, d) for s, d, _t in trace.starts()]
    assert [s for s, _d in dispatched[:3]] == STAGES, (
        f"前 3 局应当每关各一局（先测量、再按差额走）：{[s for s, _d in dispatched[:3]]}"
    )

    per_stage: dict[int, list[int]] = {s: [] for s in STAGES}
    for stage, seed in dispatched:
        per_stage[stage].append(seed)
    assert {s: len(v) for s, v in per_stage.items()} == {2000: 3, 2001: 12, 2002: 6}
    for stage in STAGES:
        assert per_stage[stage] == stage_seeds(ROTATE, IT, stage, 0, len(per_stage[stage])), (
            f"stage {stage} 的 seed 不是该关连续流的前缀：{per_stage[stage]}"
        )

    # ---- 账本：满额、零浪费（一局一拍 ⇒ 停点就在配额上，不多跑一局）----
    vol_report = collector.volume()
    assert vol_report["collected_by_stage"] == {2000: 300, 2001: 300, 2002: 300}
    assert vol_report["shortfall_by_stage"] == {}
    assert vol_report["wasted_samples"] == 0
    assert vol_report["mode"] == "per-game-stream"
    assert vol_report["planned_games"] == 3
    assert collector.next_game() is None, "所有关满额 ⇒ 终局"

    # ---- 轮报/产物：真 verify_shards（实产 == 实际派发）与真聚合 ----
    report = info["report"]
    assert report["shards"] == 21
    assert report["games"] == 21
    assert len(report["perGame"]) == 21
    assert len(report["perGameSecs"]) == 21, "逐局耗时按派发下标展开（流式局数是现场长的）"
    assert report["totalSamples"] == 900  # 300×3：恰好是 target
    assert info["shard_dirs"] and len(info["shard_dirs"]) == 21


def test_declaration_is_the_dispatched_set_even_when_the_count_matches_the_plan(
    tmp_path: Path, hermetic_node: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 声明集按**内容**重建，不按条数（2026-10-27，本轮踩到的坑）。

    构造「局数恰好等于计划」：est 写 300（⇒ 计划每关 1 局），而每局实测就给满 300 ⇒ 流式也
    只派 1 局/关。**条数相同，但 `--out` 不同**（计划 `w0..w2`、现场 `w3..w5`）。

    旧写法只在 `len(argvs) != len(templates)` 时重建声明 ⇒ 这一档会留着计划的 argv：
    `collect_reports` 去 `job/w0/_rl_report.json` 读一份根本不存在的报告 ⇒ 整轮拒收（真机：
    每关计划 1 局、实测就满额的冷启动轮）。本条用例就是那条路径的复现。
    """
    vol = volume_block(ests={s: 300 for s in STAGES})
    spec = build_spec(vol=vol, workers=2)
    assert len(spec["argv"]) == len(STAGES), "计划应当是每关 1 局"

    trace = _Trace()
    trace.samples = {s: 300 for s in STAGES}
    monkeypatch.setattr(iter_rollout, "_run_one_game_bounded", _fake_game(trace))
    info, _logs, collector = _run_round(tmp_path, trace, vol=vol, workers=2)

    assert len(trace.starts()) == len(STAGES), f"每关一局就满额：{trace.starts()}"
    report = info["report"]
    assert report["shards"] == len(STAGES)
    assert report["totalSamples"] == 900
    assert len(info["shard_dirs"]) == len(STAGES)
    # 现场派的 `--out` 与计划里的不重号（真机上的判据：重号 = 覆盖别人的产出）
    outs = {argv[argv.index("--out") + 1] for argv in spec["argv"]}
    assert outs == {"w0", "w1", "w2"}
    assert collector.volume()["dispatched_games"] == len(STAGES)


def test_zero_sample_stage_is_capped_and_reported_not_silently_short(
    tmp_path: Path, hermetic_node: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一关永远 0 样本：撞硬顶收场，但轮报里 `capped_stages` 说得清（不静默短采）。"""
    vol = volume_block()
    trace = _Trace()
    trace.samples = {2000: 300, 2001: 100, 2002: 300}
    trace.zero_sample_stages = {2001}
    monkeypatch.setattr(iter_rollout, "_run_one_game_bounded", _fake_game(trace))
    _info, _logs, collector = _run_round(tmp_path, trace, vol=vol, workers=4)
    vr = collector.volume()
    assert vr["capped_stages"] == [2001]
    assert vr["shortfall_by_stage"].get(2001, 0) > 0
    # 另外两关照常满额（短采只发生在那一个坏关上）
    assert vr["collected_by_stage"][2000] >= 300
    assert vr["collected_by_stage"][2002] >= 300

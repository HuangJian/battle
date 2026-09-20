"""rl/volume_waves.py —— 按样本量动态采集的纯逻辑（plan/dynamic-rollout-volume）。

三条不变量各有一节：
  ① **老行为逐字节不变**：无键课程走 build_pairs 原路（含冻结摘要，任何流变更即红）；
  ② **跨关独立**：给 A 关加波绝不改变 B 关任何一局的种子；
  ③ **可 replay**：全部决策是 (配置, it, 账本, 历史) 的纯函数（同输入 ⇒ 同输出）。
"""

from __future__ import annotations

import hashlib
import json
import types
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from rl.commit_journal import CommitJournal
from rl.config import CourseConfig, corpus_identity_fp
from rl.course import build_pairs
from rl.loop_core import TrainingLoop
from rl.volume_waves import (
    DEFAULT_MAX_WAVES,
    STOP_GAME_CAP,
    STOP_QUOTA_MET,
    STOP_WAVE_CAP,
    VOLUME_RULE_V1,
    TopUpPlan,
    default_game_cap,
    initial_games,
    plan_topup,
    target_per_stage,
    terminate,
    topup_games,
    wave_pairs,
    wave_seeds,
)
from tests.conftest import bp_args

# ─────────────────── ① 配额数学 ───────────────────


def test_target_per_stage_ceil() -> None:
    assert target_per_stage(600000, 4) == 150000
    assert target_per_stage(10, 3) == 4  # ceil(10/3)
    assert target_per_stage(9, 3) == 3
    # P2-6：target=0 不再静默返回 0，改响亮报错（见 test_target_per_stage_rejects_*）
    with pytest.raises(ValueError):
        target_per_stage(0, 4)
    with pytest.raises(ValueError):
        target_per_stage(100, 0)


def test_initial_games_ceil_and_floor() -> None:
    # 60 万 samples ÷ 4 关 ÷ 967 samples/局 ≈ 155.1 ⇒ ceil 156/关（「≈155」是取整口语）
    assert initial_games(600000, 4, 967) == 156
    assert initial_games(600000, 4, 900) == 167
    # 目标小于一局 ⇒ 地板 1（永远至少采一局，否则初波为空）
    assert initial_games(3, 4, 900) == 1
    # 整除不虚多一局
    assert initial_games(4000, 4, 1000) == 1
    assert initial_games(8000, 4, 1000) == 2
    with pytest.raises(ValueError):
        initial_games(600000, 4, 0)
    with pytest.raises(ValueError):
        initial_games(600000, 0, 900)
    # P2-6：target ≤ 0 响亮报错（原先静默 → 每关立即 quota_met 停采）
    with pytest.raises(ValueError):
        initial_games(0, 4, 900)
    with pytest.raises(ValueError):
        initial_games(-1, 4, 900)


def test_est_is_not_floored() -> None:
    """est **不得**被钳到任何下限 —— 配额反解就是 `G0 = ceil(每关目标 / est)` 的原式。

    2026-09-15 回退 P2-b：曾加过 `MIN_EST_SAMPLES_PER_GAME = 100`，理由是"est 过小会让
    G0 与 cap 同步爆炸、硬顶追不上"。但那个前提把**合法的小 est** 误当成故障值：
    `e2e/test_volume_e2e.py::test_quota_converges_within_one_wave` 用 est=20 的合法值
    证伪了它 —— 钳到 100 后 `G0 = ceil(100/100) = 1`，初波 1 局/关补不到达标线，
    集成测试当场红（该测试的契约正是 `G0 = 5`）。

    若真要在乎"est 配得过小 ⇒ G0 爆炸"，正确位置是**配置层校验**（CourseConfig 解析时
    判 est 与关卡量级是否相称），而不是在这个纯函数里一刀切 —— 那会连带改掉合法路径。
    """
    # est=20 是 e2e 用的合法小值：每关目标 100 ⇒ 恰好 5 局（钳位会把它变成 1）
    assert initial_games(200, 2, 20) == 5
    # est 越小 G0 按原式越大，**不封顶**
    assert initial_games(600000, 4, 10) == 15000
    assert initial_games(600000, 4, 1) == 150000
    # topup 同口径不钳：剩余 1000 / est 20 = 50
    assert topup_games(1000, 20) == 50
    # 真值不受影响（六位数 target / 两位数 est 是常态）
    assert initial_games(600000, 4, 967) == 156
    # cap 按设计随 G0 缩放（×DEFAULT_GAME_CAP_MULT）——它是"单波上限"，**不是**给 G0 封顶的
    assert default_game_cap(initial_games(200, 2, 20)) == 5 * 4


def test_topup_games_ceil_and_zero() -> None:
    assert topup_games(1000, 900) == 2  # ceil
    assert topup_games(900, 900) == 1
    assert topup_games(1, 900) == 1
    assert topup_games(0, 900) == 0
    assert topup_games(-5, 900) == 0
    with pytest.raises(ValueError):
        topup_games(100, 0)


def test_default_game_cap() -> None:
    assert default_game_cap(155) == 155 * 4
    assert default_game_cap(0) == 1  # 不产生 0 帽（0 在 terminate 里 = 无帽语义）


def test_target_per_stage_rejects_nonpositive_target() -> None:
    """P2-6：`target_transitions ≤ 0` 响亮报错（原先 max(0,·) 静默降级）。"""
    assert target_per_stage(600000, 4) == 150000
    with pytest.raises(ValueError):
        target_per_stage(-100, 4)


# ─────────────────── ① T9 量纲钉死（600000 / 4 / 980） ───────────────────


def test_t9_dims_pinned_600000_4_980() -> None:
    """★ T9：`target_transitions` 与 `est_samples_per_game` **同单位 = samples**（= ticks/K）。

    评审复算用例（plan/x3-power-followup §T9）：`target=600000`、4 关、x3 腿局均
    **980 ticks**。exporter 按 K 降采样（x3 实测 samples/ticks ≈ 0.1007 ⇒ K≈10）
    ⇒ 局均 samples ≈ 98。**新语义**（分母 = samples）：

        G0 = ceil(150000/98) = 1531 局/关 ⇒ 1531×98 = 150038 ≥ 150000 —— 一波即达标。

    **旧语义**（分母 = ticks 980）给出 G0=154 局/关 ⇒ 单波只采 154×98 = 15092
    （达标线的 ~10%）；而补波量同样按那个错估缩放（`ceil(缺口/980)`），补满波次
    上限仍是零头 ⇒ 终以 `wave_cap` 收场（评审的「兑现 37% 触顶」用例）。本测试不
    钉那个小数（它取决于 K 与 est 的细节），只钉**方向与量级**：新语义一波收敛、
    旧语义补满法定波次仍不达标。
    """
    target, n_stages = 600000, 4
    per_stage = target_per_stage(target, n_stages)
    est_samples = 98  # = 980 ticks / K，K=10

    # 新语义：一波达标，且过冲 < 1 局（ceil 的必然）
    g0 = initial_games(target, n_stages, est_samples)
    assert g0 == 1531
    assert g0 * est_samples >= per_stage
    assert (g0 - 1) * est_samples < per_stage

    # plan/dynamic-rollout-volume §2.1 的示例读数（900 ticks ≈ 90 samples）同样一波收敛
    assert initial_games(target, n_stages, 90) == 1667
    assert per_stage <= 1667 * 90

    # 旧语义（ticks 填进 samples 分母）：初波只采到达标线的 ~10%
    g0_ticks = initial_games(target, n_stages, 980)
    assert g0_ticks == 154
    got = g0_ticks * est_samples
    assert got < per_stage // 5
    # 补满波次上限也追不上（补波同样按 ticks 尺度缩放 ⇒ 永远落后一个量级）
    waves = 1
    while waves < DEFAULT_MAX_WAVES:
        got += topup_games(per_stage - got, 980) * est_samples
        waves += 1
    assert waves == DEFAULT_MAX_WAVES == 3
    assert got * 3 < per_stage  # 终局仍不到达标线的 1/3


def test_t9_old_ticks_key_name_is_rejected() -> None:
    """旧键名照写 = 启动期响亮报错（`extra="forbid"`）——绝不静默当 samples 用掉。

    这是「改名」这条选路的护栏：若旧键被静默接受，10× 误采会无声复辟（既有课程
    文件一个字不改就继续跑错量纲）。
    """
    # 走 `model_validate`（与 `load_course` 同一条「原始 dict → 校验」路径），
    # 免得 mypy 把「故意传旧键」当成调用错误。
    with pytest.raises(ValidationError):
        CourseConfig.model_validate({"target_transitions": 600000, "est_ticks_per_game": 980})
    # 对照：换成新键名、同一个数值就是合法的（报错来自键名，不是数值）
    ok = CourseConfig(target_transitions=600000, est_samples_per_game=980)
    assert ok.est_samples_per_game == 980


# ─────────────────── ① 终止谓词 ───────────────────


def _term(**kw: int) -> str | None:
    base = dict(
        collected=0, target=100, waves_done=0, max_waves=3, games_done=1, max_games_per_stage=0
    )
    base.update(kw)
    return terminate(**base)


def test_terminate_continue_when_open() -> None:
    assert _term() is None


def test_terminate_quota_met_wins_over_caps() -> None:
    """配额达成永远优先：同时触顶但已达标不是「未满」事件。"""
    assert (
        _term(collected=100, waves_done=99, games_done=99, max_games_per_stage=1) == STOP_QUOTA_MET
    )
    assert _term(collected=101, waves_done=99) == STOP_QUOTA_MET


def test_terminate_game_cap_before_wave_cap() -> None:
    assert _term(games_done=40, max_games_per_stage=40, waves_done=99, max_waves=3) == STOP_GAME_CAP
    assert _term(waves_done=3, max_waves=3) == STOP_WAVE_CAP
    # 0 = 无帽（默认规则由调用方折算成具体数字后传入）
    assert _term(games_done=10**6, max_games_per_stage=0) is None


# ─────────────────── ② 跨关独立性 ───────────────────


def test_wave_seed_stream_keyed_and_deterministic() -> None:
    a = wave_seeds(4242, 9, 3, 0, 5)
    b = wave_seeds(4242, 9, 3, 0, 5)
    assert a == b
    assert a == wave_seeds(4242, 9, 3, 0, 5)  # 纯函数：无隐藏状态
    # 四个键各自扰动都换种子
    assert a != wave_seeds(4243, 9, 3, 0, 5)  # rotate_seed
    assert a != wave_seeds(4242, 10, 3, 0, 5)  # it
    assert a != wave_seeds(4242, 9, 4, 0, 5)  # stage
    assert a != wave_seeds(4242, 9, 3, 1, 5)  # wave
    # 初波与补波必须落在不同 tag（否则补波可能撞上初波的签）
    assert wave_seeds(4242, 9, 3, 1, 5) != wave_seeds(4242, 9, 3, 0, 5)
    assert len(set(a)) == len(a)  # numpy 抽样在 2**30 值域内不重复
    assert all(1 <= s < 2**30 for s in a)
    assert wave_seeds(4242, 9, 3, 0, 0) == []
    assert wave_seeds(4242, 9, 3, 0, -3) == []


def test_stage_streams_are_independent_of_each_other() -> None:
    """★ 核心不变量：给 A 关加波，B 关任何一局的种子不变（计划 §2.2.5）。

    单条顺序流的实现会在这里红：A 关多抽 n 签就会把 B 关的流位置整体推后。
    """
    b_before = wave_seeds(777, 12, 5, 0, 20) + wave_seeds(777, 12, 5, 1, 20)

    # A 关（stage 4）先初波、再补两波（不同局数），全程不改 B 关（stage 5）
    for wave, n in ((0, 7), (1, 3), (2, 11), (3, 1)):
        _ = wave_seeds(777, 12, 4, wave, n)

    b_after = wave_seeds(777, 12, 5, 0, 20) + wave_seeds(777, 12, 5, 1, 20)
    assert b_before == b_after


def test_wave_stream_is_not_a_single_sequential_stream() -> None:
    """按关独立 ⇒ 同一关同波的前 n 签与 n 无关（可增量补波而不重抽）。"""
    assert wave_seeds(99, 4, 2, 1, 3) == wave_seeds(99, 4, 2, 1, 50)[:3]


def test_wave_pairs_stage_sorted_and_deterministic() -> None:
    plan = {7: 2, 3: 1}
    pairs = wave_pairs(314, 5, plan, 1)
    assert pairs == wave_pairs(314, 5, plan, 1)
    assert [st for st, _ in pairs] == [3, 7, 7]  # 升序：计划内的 pair 序列也可逐字节比
    assert len(set(pairs)) == 3
    assert wave_pairs(314, 5, {}, 1) == []


# ─────────────────── ② 补波计划 ───────────────────


def _plan(**kw: object) -> TopUpPlan:
    base: dict[str, object] = dict(
        stages=[0, 1, 2, 3],
        collected={0: 0, 1: 0, 2: 0, 3: 0},
        target_transitions=600000,
        est_samples_per_game=967,
        waves_done=1,
        games_done={0: 155, 1: 155, 2: 155, 3: 155},
        initial_g0=155,
    )
    base.update(kw)
    return plan_topup(**base)  # type: ignore[arg-type]


def test_plan_topup_shortfall_math() -> None:
    # 每关达标线 150000 samples；已结算 100000 ⇒ 缺口 50000 ⇒ ceil(50000/967) = 52 局
    plan = _plan(collected={0: 100000, 1: 100000, 2: 100000, 3: 100000})
    assert plan.games_by_stage == {0: 52, 1: 52, 2: 52, 3: 52}
    assert plan.games_total == 208
    assert plan.shortfall == {0: 50000, 1: 50000, 2: 50000, 3: 50000}
    assert plan.stopped == {}
    assert plan.capped is False


def test_plan_topup_per_stage_independence() -> None:
    """短局关淹不了长局关：已达标的关不进补波表，也不占用它的预算。"""
    plan = _plan(collected={0: 150000, 1: 10, 2: 0, 3: 150000})
    assert set(plan.games_by_stage) == {1, 2}
    assert plan.stopped == {0: STOP_QUOTA_MET, 3: STOP_QUOTA_MET}
    assert plan.shortfall[0] == 0 and plan.shortfall[3] == 0
    assert plan.games_by_stage[1] == 156  # ceil(149990/967)


def test_plan_topup_all_met_is_empty() -> None:
    plan = _plan(collected={s: 150000 for s in range(4)})
    assert plan.games_by_stage == {}
    assert set(plan.stopped.values()) == {STOP_QUOTA_MET}


def test_plan_topup_wave_cap() -> None:
    plan = _plan(waves_done=DEFAULT_MAX_WAVES)
    assert plan.games_by_stage == {}
    assert set(plan.stopped.values()) == {STOP_WAVE_CAP}
    assert plan.capped is False  # 波次上限不是硬顶事件


def test_plan_topup_game_cap_stops_and_flags() -> None:
    plan = _plan(games_done={s: 620 for s in range(4)}, max_games_per_stage=620)
    assert plan.games_by_stage == {}
    assert set(plan.stopped.values()) == {STOP_GAME_CAP}
    assert plan.capped is True  # 配额未满 + 触顶 ⇒ 响亮日志/事件打标


def test_plan_topup_cap_truncates_current_wave() -> None:
    """硬顶是硬顶：本波也不许越界（不搞「下一波才停」的软顶）。"""
    plan = _plan(games_done={0: 600, 1: 0, 2: 0, 3: 0}, max_games_per_stage=620)
    assert plan.games_by_stage[0] == 20  # 只剩 20 局额度，而缺口要 52 局
    assert 0 not in plan.stopped


def test_plan_topup_default_cap_from_g0() -> None:
    """max_games_per_stage = 0 ⇒ 默认 初波 × 4（155 → 620）。"""
    plan = _plan(games_done={s: 620 for s in range(4)})
    assert set(plan.stopped.values()) == {STOP_GAME_CAP}
    assert plan.capped is True
    plan2 = _plan(games_done={s: 619 for s in range(4)})
    assert plan2.games_by_stage == {0: 1, 1: 1, 2: 1, 3: 1}


def test_plan_topup_requires_stages() -> None:
    with pytest.raises(ValueError):
        _plan(stages=[])


def test_plan_topup_is_pure_replayable() -> None:
    """★ ③ 可 replay：同输入 ⇒ 同输出（重启后重算，不重新抛硬币）。"""
    a = _plan(collected={0: 1234, 1: 99887, 2: 150000, 3: 42})
    b = _plan(collected={0: 1234, 1: 99887, 2: 150000, 3: 42})
    assert a == b


# ─────────────────── ③ WAL round-trip ───────────────────


def test_volume_wave_wal_roundtrip(tmp_path: Path) -> None:
    """补波决策进 commit journal：finish 后不再是 pending；停在波中时可见（含载荷）。"""
    path = tmp_path / "commit_journal.jsonl"
    j = CommitJournal(path)
    payload = {
        "wave_idx": 1,
        "games": {"0": 52, "1": 52},
        "collected": {"0": 100000, "1": 100000},
        "shortfall": {"0": 50000, "1": 50000},
        "target": 600000,
        "est": 967,
    }
    j.start("volume_wave", "9:w1", **payload)
    pending = j.pending()
    assert pending == [{"phase": "volume_wave", "round": "9:w1"}]
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["event"] == "commit_journal" and rec["op"] == "start"
    for k, v in payload.items():  # schema round-trip：决策载荷逐键可读
        assert rec[k] == v
    j.finish("volume_wave", "9:w1", games={"0": 52, "1": 52})
    assert j.pending() == []


def test_volume_wave_wal_does_not_mask_ppo_pending(tmp_path: Path) -> None:
    """补波相位与 PPO 相位互不遮蔽：两者各自 start 未 finish 都在 pending 里。"""
    j = CommitJournal(tmp_path / "commit_journal.jsonl")
    j.start("volume_wave", "9:w1", est=967)
    j.finish("volume_wave", "9:w1")
    j.start("ppo_remote", "9")
    assert j.pending() == [{"phase": "ppo_remote", "round": "9"}]


# ─────────────────── ① 老行为逐字节不变（DoD 第一条） ───────────────────


def _build_pairs_args(rotate_stages: int = 35, seed_rotate: int = 0) -> types.SimpleNamespace:
    """`build_pairs` 的最小 duck-typed args（bp_args 基础上改两条路径开关）。"""
    a = bp_args()
    a.rotate_stages = rotate_stages
    a.seed_rotate = seed_rotate
    return a


def test_build_pairs_frozen_rotate_path() -> None:
    """★ 无键课程走 build_pairs 原路：输出冻结（任何流/顺序变更立即红）。

    这条摘要就是「缺席 = 老行为逐字节不变」的可执行形式：动态采集一行都不许
    改动无键课程的抽签流。
    """
    pairs = build_pairs(_build_pairs_args(), 60, 1787503550)
    assert len(pairs) == 105
    digest = hashlib.sha256(repr(pairs).encode()).hexdigest()
    assert digest == "7f37503f3622695bfb8463cd87e1d31e86f3d99f883cdac7b0048455af49fc5e"


def test_build_pairs_frozen_explicit_seed_rotate_path() -> None:
    """显式模式（--seed-rotate N）：x3-power 类课程走的正是这条。"""
    pairs = build_pairs(_build_pairs_args(rotate_stages=0, seed_rotate=60), 7, 4242)
    assert len(pairs) == 240  # 4 关 × 60
    digest = hashlib.sha256(repr(pairs).encode()).hexdigest()
    assert digest == "32c805123a7aee231575824efce90c012ac700c19ae9c52937da211432c796e4"


def test_build_pairs_frozen_fixed_seeds_path() -> None:
    pairs = build_pairs(_build_pairs_args(rotate_stages=0), 3, 99)
    assert len(pairs) == 16
    digest = hashlib.sha256(repr(pairs).encode()).hexdigest()
    assert digest == "30648a824e6ec89bf40489292044af4de6166b69c935b7ebfd0a9e9c2f17c25b"


def test_build_pairs_frozen_deltas_are_still_behavioral() -> None:
    """冻结摘要不是死值：it / rotate_seed 变了，pair 必须真的变（防写死假绿）。"""
    a = build_pairs(_build_pairs_args(rotate_stages=0, seed_rotate=60), 7, 4242)
    assert a != build_pairs(_build_pairs_args(rotate_stages=0, seed_rotate=60), 8, 4242)
    assert a != build_pairs(_build_pairs_args(rotate_stages=0, seed_rotate=60), 7, 4243)


# ─────────────────── 课程键 + 语料身份 ───────────────────


def test_course_volume_keys_default_off() -> None:
    c = CourseConfig()
    assert (c.target_transitions, c.est_samples_per_game, c.max_games_per_stage) == (0, 0, 0)
    assert "target_transitions" not in c.flat_overrides()  # 缺席 = 不覆盖 argparse


def test_course_volume_keys_flat_overrides_when_explicit() -> None:
    c = CourseConfig(target_transitions=600000, est_samples_per_game=967, max_games_per_stage=0)
    ov = c.flat_overrides()
    assert ov["target_transitions"] == 600000
    assert ov["est_samples_per_game"] == 967
    assert ov["max_games_per_stage"] == 0  # 显式出现在 JSON 里就照写（含 0）


def test_course_volume_requires_est() -> None:
    with pytest.raises(ValidationError):
        CourseConfig(target_transitions=600000)  # est 缺失 = 配额无法反解
    with pytest.raises(ValidationError):
        CourseConfig(target_transitions=600000, est_samples_per_game=0)
    # 关掉模式时 est 无意义，允许缺席
    assert CourseConfig(target_transitions=0, est_samples_per_game=0).target_transitions == 0


def test_course_volume_keys_reject_negative() -> None:
    with pytest.raises(ValidationError):
        CourseConfig(target_transitions=-1)
    with pytest.raises(ValidationError):
        CourseConfig(est_samples_per_game=-5)
    with pytest.raises(ValidationError):
        CourseConfig(max_games_per_stage=-2)


def _payload_ref(course: CourseConfig) -> dict:
    """`corpus_identity_fp` 的独立重实现（tests/stages.test.ts 式对照，独立于实现）。"""
    import schema

    stages = (
        [s.model_dump() for s in course.stages]
        if isinstance(course.stages, list)
        else course.stages
    )
    return {
        "obs_schema_major": schema.OBS_SCHEMA_MAJOR,
        "obs_schema_fingerprint": schema.SCHEMA_FINGERPRINT,
        "mode": course.mode,
        "stages": stages,
        "difficulty": course.difficulty,
        "max_ticks": course.max_ticks,
        "seed_rotate": course.seed_rotate,
        "seeds": course.seeds,
        "player": course.player.model_dump(),
        "dodge": course.dodge,
        "reward": course.reward.model_dump(),
    }


def _fp_of(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_corpus_fp_unchanged_for_courses_without_volume_keys() -> None:
    """★ 无键课程的指纹必须与「今日 payload」逐字节一致（D14 血缘不许整体漂移）。

    这一条挡的是最贵的错误形态：把 volume 键无条件塞进 payload，会让**所有**既有
    课程的 corpus_fp 一起变——在跑的腿把已落盘 shard 判成异身份、云端 job 全拒。
    """
    c = CourseConfig()
    assert corpus_identity_fp(c) == _fp_of(_payload_ref(c))


def test_corpus_fp_carries_volume_keys_when_active() -> None:
    c = CourseConfig(target_transitions=600000, est_samples_per_game=967)
    ref = _payload_ref(c)
    ref["volume_rule"] = VOLUME_RULE_V1
    ref["target_transitions"] = 600000
    assert corpus_identity_fp(c) == _fp_of(ref)
    assert corpus_identity_fp(c) != corpus_identity_fp(CourseConfig())


def test_corpus_fp_ignores_volume_tuning_knobs() -> None:
    """est（运行期被 trailing 均值覆盖的兜底估计）与 max_games（硬顶）不进身份。"""
    a = CourseConfig(target_transitions=600000, est_samples_per_game=967)
    b = CourseConfig(target_transitions=600000, est_samples_per_game=1400, max_games_per_stage=99)
    assert corpus_identity_fp(a) == corpus_identity_fp(b)


# ─────────────────── loop 接线（TrainingLoop 真方法 + 桩 self） ───────────────────
#
# 这里用桩 self 直接调 TrainingLoop 的**真方法**（unbound 绑定）：不拉 torch 模型、
# 不拉 bun，但走的是生产代码路径（阈值/日志/WAL/报告合并全是真实现），是 v1 能在
# 单测里拿到的最强证据。真实采集的 e2e（FakeAgent + bun sim）不在此处——那属于
# plan §3-P2 的微课试点。


def _manifest(
    d: Path,
    stage: int,
    seed: int,
    wver: str,
    n_samples: int,
    outcome: str = "timeout",
) -> None:
    p = d / f"rl_s{stage:02d}_seed{seed}"
    p.mkdir(parents=True, exist_ok=True)
    (p / "manifest.json").write_text(
        json.dumps({
            "stage": stage,
            "seed": seed,
            "wver": wver,
            "nSamples": n_samples,
            "outcome": outcome,
        }),
        encoding="utf-8",
    )


class _StubLoop:
    """最小 TrainingLoop 替身：只带动态采集接线用得着的属性/方法。

    `_dispatch_volume_wave` 用假 shard 模拟结算（每局 `samples` 个 transitions），
    其余全部是生产实现。
    """

    # —— 生产方法（unbound 绑定到桩 self：走的不是复制品）——
    # `cast(Any, self)` 是必要的：mypy 看不到「桩 self 具备真方法要的全部属性」这件事，
    # 而这些方法确实只碰 args/_traj_dir/_report/_volume_*/_commit_journal（无模型/无 bun）。

    def _volume_active(self) -> bool:
        return TrainingLoop._volume_active(cast(Any, self))

    def _volume_stages(self) -> list[int]:
        return TrainingLoop._volume_stages(cast(Any, self))

    def _volume_est_samples(self) -> int:
        return TrainingLoop._volume_est_samples(cast(Any, self))

    def _iteration_pairs(self, it: int) -> list[tuple[int, int]]:
        return TrainingLoop._iteration_pairs(cast(Any, self), it)

    def _volume_topup(self, it: int, dist_cfg: dict | None) -> None:
        TrainingLoop._volume_topup(cast(Any, self), it, dist_cfg)

    def _volume_journal_replay(self, it: int) -> Any:
        return TrainingLoop._volume_journal_replay(cast(Any, self), it)

    def _volume_stage_ests_map(self) -> dict[int, int]:
        return TrainingLoop._volume_stage_ests_map(cast(Any, self))

    def _volume_collect_continuous(self, it: int, dist_cfg: dict | None) -> None:
        TrainingLoop._volume_collect_continuous(cast(Any, self), it, dist_cfg)

    def __init__(
        self,
        tmp: Path,
        *,
        target: int = 0,
        est: int = 967,
        samples: int = 500,
        max_games_per_stage: int = 0,
        curriculum_stages: str = "",
        it: int = 1,
    ) -> None:
        self.args = types.SimpleNamespace(
            target_transitions=target,
            est_samples_per_game=est,
            max_games_per_stage=max_games_per_stage,
            stages="0-3",
            seeds="0-3",
            seed_rotate=0,
            rotate_stages=0,
            total_stages=35,
            seeds_per_stage=3,
            curriculum_stages=curriculum_stages,
            curriculum_start=4,
            curriculum_every=8,
            curriculum_grow=4,
            collect_only=0,
            out=str(tmp / "weights.json"),
            traj=str(tmp / "traj"),
        )
        self.it = it
        self.samples = samples
        self._rotate_seed = 4242
        self._traj_root = Path(self.args.traj)
        self._jsonl_path = self._traj_root / "training_log.jsonl"
        self._traj_dir = self._traj_root / f"it{it}"
        self._traj_dir.mkdir(parents=True, exist_ok=True)
        self._report: dict = {
            "games": 0,
            "winRate": 0.0,
            "outcomes": {},
            "totalSamples": 0,
            "totalTicks": 0,
            "scoreList": [],
            "dimLists": {},
        }
        self._stream_meta: dict | None = None
        self._course_fp: str | None = None
        self._extra_wver = None
        self._volume_target: int | None = None
        self._volume_collected: int | None = None
        self._volume_waves: int = 0
        self._volume_g0 = 0
        self._volume_est = 0
        self._volume_capped = False
        # 与生产同路径（loop_steps._commit_journal = <traj_dir>/commit_journal.jsonl）
        self._journal = CommitJournal(self._traj_dir / "commit_journal.jsonl")
        self.dispatched: list[list[tuple[int, int]]] = []

    def _commit_journal(self) -> CommitJournal:
        return self._journal

    def _dispatch_volume_wave(
        self, it: int, pairs: list[tuple[int, int]], dist_cfg: dict | None
    ) -> dict:
        self.dispatched.append(list(pairs))
        for stage, seed in pairs:
            _manifest(self._traj_dir, stage, seed, _WVER, self.samples)
        samples = len(pairs) * self.samples
        return {
            "games": len(pairs),
            "winRate": 0.0,
            "outcomes": {"timeout": len(pairs)},
            "totalSamples": samples,
            # K=10 的 stub 惯例：ticks = 10×samples。配额只认 samples，所以故意让
            # 两者不同——任何误读 ticks 的路径都会在断言里 10× 暴露。
            "totalTicks": samples * 10,
            "scoreList": [],
            "dimLists": {},
        }


_WVER = "wver-volume-test"


@pytest.fixture
def _patch_wver(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定 wver（真实实现要读盘上的权重文件，测试不为此造一个假权重）。"""
    import dist_common

    monkeypatch.setattr(dist_common, "weights_fingerprint", lambda _p: _WVER)


def test_settled_totals_accept_both_manifest_schemas(tmp_path: Path) -> None:
    """★ 账本必须认两种落盘 schema（`dist_common.write_shard` 原样写 agent 的 manifest）：

    单局 `nSamples`（TS exporter 正规形）与聚合单局 `totalSamples`（队列/远端 path）。
    只认前者 ⇒ 后者那些关永远「零样本」、补波永不达标（白烧到波次上限）——2026-09-15
    e2e 实测踩到（`e2e/test_volume_e2e.py::test_short_stage_does_not_starve_long_stage`）。
    """
    from rl.resume import settled_stage_totals

    traj = tmp_path / "traj"
    wver = "w" * 12
    for stage, seed, mm in (
        (0, 1, {"nSamples": 40, "ticks": 40, "outcome": "timeout"}),
        (1, 2, {"totalSamples": 55, "games": 1, "outcomes": {"timeout": 1}}),
        (2, 3, {"games": 1, "outcomes": {"timeout": 1}}),  # 两者都缺 ⇒ 0（不猜）
    ):
        d = traj / f"rl_s{stage:02d}_seed{seed}"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"wver": wver, "stage": stage, "seed": seed, **mm}), encoding="utf-8"
        )
    assert settled_stage_totals(traj, wver) == {0: (1, 40), 1: (1, 55), 2: (1, 0)}


def test_iteration_pairs_identity_without_volume(tmp_path: Path, _patch_wver: None) -> None:
    """★ 无键课程：走 build_pairs 原路，逐字节同一（接线不许动老路径）。"""
    stub = _StubLoop(tmp_path)
    assert stub._iteration_pairs(9) == build_pairs(stub.args, 9, stub._rotate_seed)
    assert stub._volume_target is None and stub._volume_collected is None


def test_iteration_pairs_volume_initial_wave(tmp_path: Path, _patch_wver: None) -> None:
    """volume 课程仍产出 node/export 预排表；串行连续采集不预置 waves=1。"""
    stub = _StubLoop(tmp_path, target=600000, est=967)
    pairs = stub._iteration_pairs(3)
    assert stub._volume_waves == 0
    assert stub._volume_g0 == 156
    assert stub._volume_target == 600000
    assert pairs == wave_pairs(stub._rotate_seed, 3, {s: 156 for s in range(4)}, 0)
    assert len(pairs) == 4 * 156
    assert {st for st, _ in pairs} == {0, 1, 2, 3}


def test_iteration_pairs_rejects_gated_window_modes(tmp_path: Path, _patch_wver: None) -> None:
    """curriculum/rotate 门控窗口 + 配额分关 v1 不兼容 → 响亮失败（不静默错分）。"""
    stub = _StubLoop(tmp_path, target=600000, curriculum_stages="13,1,16")
    with pytest.raises(SystemExit):
        stub._iteration_pairs(1)


def test_volume_stages_rejects_missing_or_empty_stages(tmp_path: Path) -> None:
    """P2-c：`--stages` 缺席/空集 → 响亮退出，不再静默退成硬编码 4 关。

    原先 fallback `parse_range(str(... or "0-3"))`：缺 --stages 时静默猜 4 关 ⇒
    分关配额分母错、采集量对不上目标而不报错（实测本腿恒有值所以没踩到，
    但静默猜关数正是最难发现那类失效）。
    """
    for bad in ("", "   ", "junk", "5-4"):  # 空 / 空白 / 不可解析 / 逆序区间
        stub = _StubLoop(tmp_path, target=600000)
        stub.args.stages = bad
        with pytest.raises(SystemExit):
            stub._volume_stages()


def test_volume_stages_uses_explicit_stages(tmp_path: Path) -> None:
    """显式 --stages 正常解析（回归：P2-c 改动不得误伤正常路径）。"""
    stub = _StubLoop(tmp_path, target=600000)
    stub.args.stages = "2000-2003"
    assert stub._volume_stages() == [2000, 2001, 2002, 2003]


def test_continuous_restart_quota_met_backfills_winrate_from_shards(
    tmp_path: Path, _patch_wver: None
) -> None:
    """★ 配额已满重启：本进程零新采不得记 winRate=0（x20-steady it75→it76）。

    停机前 quota 已采满（50086/48000）⇒ 新进程 continuous while 立刻 break、
    combined=None ⇒ adopt_volume_report(None) 把除零保护的 0.0 写进账本。
    磁盘上本轮 shard 的 outcome 必须回填——「打了 0 局」与「打了 N 局全输」
    在数值上必须可区分。
    """
    stub = _StubLoop(tmp_path, target=200, est=20, samples=200, it=76)
    # 上一进程已把本轮采满并落盘：4 关 × 2 局，胜败各半，nSamples 远超分关配额。
    for stage in range(4):
        for seed in range(2):
            outcome = "stage_clear" if seed == 0 else "timeout"
            _manifest(stub._traj_dir, stage, seed, _WVER, 200, outcome=outcome)
    stub._volume_collect_continuous(76, None)
    assert stub.dispatched == []  # 配额已满 ⇒ 零新采
    assert stub._volume_waves == 0
    # 报告口径 = 盘上本轮 shard，不是本进程 combine([]) 的空壳
    assert stub._report["games"] == 8
    assert stub._report["outcomes"].get("stage_clear") == 4
    assert stub._report["outcomes"].get("timeout") == 4
    assert stub._report["winRate"] == 0.5
    assert stub._report["totalSamples"] == 8 * 200


def test_volume_topup_quota_met_in_first_wave(tmp_path: Path, _patch_wver: None) -> None:
    """初波就达标：不补波，只记账（est 估准的正常情形）。"""
    stub = _StubLoop(tmp_path, target=600000, est=967, samples=967)
    stub._iteration_pairs(1)
    stub._volume_waves = 1  # wave 测试：初波已跑（生产连续配额不再预置）
    _settle_first_wave(stub)
    stub._volume_topup(1, None)
    assert stub.dispatched == []  # 无需补波
    assert stub._volume_collected == 4 * 156 * 967
    assert stub._journal.pending() == []


def test_volume_topup_iterates_until_wave_cap(tmp_path: Path, _patch_wver: None) -> None:
    """每局 samples 偏少（est 声明值偏大）⇒ 逐关补波，至多 3 波后停（不无限补）。"""
    stub = _StubLoop(tmp_path, target=600000, est=967, samples=500)
    stub._iteration_pairs(1)
    stub._volume_waves = 1
    _settle_first_wave(stub)
    stub._volume_topup(1, None)
    sizes = [len(w) for w in stub.dispatched]
    assert sizes == [4 * 75, 4 * 36]  # w1 ceil(72000/967)=75，w2 ceil(34500/967)=36（samples 口径）
    assert stub._volume_waves == 3
    assert stub._volume_collected == 4 * (156 + 75 + 36) * 500
    assert stub._volume_capped is False
    assert stub._journal.pending() == []  # 每波 start 都有配对的 finish
    # 报告已逐波并入（补波确实是本轮报告的一部分）
    assert stub._report["games"] == 4 * (156 + 75 + 36)
    assert stub._report["totalSamples"] == stub._volume_collected


def test_volume_topup_hard_cap_marks_capped(tmp_path: Path, _patch_wver: None) -> None:
    """局数硬顶：本波截断到剩余额度，触顶后停采并打标（配额未满但停）。"""
    stub = _StubLoop(tmp_path, target=600000, est=967, samples=500, max_games_per_stage=200)
    stub._iteration_pairs(1)
    stub._volume_waves = 1
    _settle_first_wave(stub)
    stub._volume_topup(1, None)
    assert [len(w) for w in stub.dispatched] == [4 * 44]  # 200-156 剩余额度
    assert stub._volume_capped is True
    assert stub._volume_waves == 2


def test_volume_topup_partial_ledger_replays_same_continuation(
    tmp_path: Path, _patch_wver: None
) -> None:
    """★ ③ 断点续跑：账本只落了一半（崩在波次中间）⇒ 续跑**不重新抛硬币**。

    plan §2.3 的三条一起验：(a) 同账本 ⇒ 同续跑（可 replay）；(b) 续跑派的每一签都
    出自该关该波同一条种子流（同源，不重抽）；(c) 已结算多的关补得少（分关独立）。

    配额缩到 target=60000 / est=967 / samples=500（原 600000）：Windows NTFS 上
    每局写一个 manifest，600000 配额 × 3 个 stub ≈ 3k 次 mkdir+write，call 7.8s
    超 5s 预算（同机 WSL <5s）。缩小后 g0=16、w2=8、w3=4，文件数 ~10×↓，语义不变：
    half(w2) = stage0/1 全额、2/3 仍缺；补波 0/1=4、2/3=8（原 36/75 的同比例结构）。
    """
    # 60000/4/967 → g0=16；初波后 short=7000 → w2=8；再 short=3000 → w3=4（w4 被
    # DEFAULT_MAX_WAVES=3 挡住）。
    _T, _EST, _S = 60000, 967, 500
    full = _StubLoop(tmp_path / "full", target=_T, est=_EST, samples=_S)
    full._iteration_pairs(1)
    full._volume_waves = 1
    _settle_first_wave(full)
    full._volume_topup(1, None)
    assert len(full.dispatched) == 2
    assert [len(w) for w in full.dispatched] == [4 * 8, 4 * 4]

    # 崩在第二波中间：只落了一半 shard（wave_pairs 按关升序 ⇒ 前一半 = stage 0/1 全额）
    half = full.dispatched[0][: len(full.dispatched[0]) // 2]
    crashed = _topup_from_ledger(tmp_path / "crashed", half, target=_T, est=_EST, samples=_S)
    again = _topup_from_ledger(
        tmp_path / "crashed-again", half, target=_T, est=_EST, samples=_S
    )

    # (a) 确定性：同账本 ⇒ 逐字节同续跑
    assert crashed.dispatched == again.dispatched
    # (b) 同源：续跑派的签都能在整跑同一波里找到（同一 (stage, wave) 流的前缀）
    full_w1 = {st: [sd for t, sd in full.dispatched[0] if t == st] for st in range(4)}
    for stage, seed in crashed.dispatched[0]:
        assert seed in full_w1[stage]
    # (c) 分关独立：stage 0/1 已结算 w2 ⇒ 缺口小、补得少；stage 2/3 仍要整波
    per_stage = {st: sum(1 for t, _ in crashed.dispatched[0] if t == st) for st in range(4)}
    assert per_stage[0] == per_stage[1] == 4  # ceil(3000/967)
    assert per_stage[2] == per_stage[3] == 8  # ceil(7000/967)
    assert (crashed._volume_collected or 0) > 0


def test_parse_wave_records_roundtrip(tmp_path: Path) -> None:
    """WAL 行 → 波次记录（本迭代、末条 op 定成败、games 字符串键转回 int）。"""
    from rl.volume_waves import WAVE_PHASE, parse_wave_records, wave_round_key

    j = CommitJournal(tmp_path / "commit_journal.jsonl")
    j.start(WAVE_PHASE, wave_round_key(9, 1), games={"0": 3, "1": 4}, wave_idx=1)
    j.finish(WAVE_PHASE, wave_round_key(9, 1), games={"0": 3, "1": 4})
    j.start(WAVE_PHASE, wave_round_key(9, 2), games={"0": 1}, wave_idx=2)
    j.start("ppo_local", "9")  # 别的相位不混入
    j.start(WAVE_PHASE, wave_round_key(8, 1), games={"0": 99})  # 别的迭代不混入
    lines = (tmp_path / "commit_journal.jsonl").read_text(encoding="utf-8").splitlines()
    lines.append('{"event":"commit_journal","op":"start"　')  # 半行/坏行
    recs = parse_wave_records(lines, 9)
    assert set(recs) == {1, 2}
    assert recs[1].finished is True and recs[1].games == {0: 3, 1: 4}
    assert recs[2].finished is False and recs[2].games == {0: 1}
    assert parse_wave_records([], 9) == {}
    assert parse_wave_records(lines, 7) == {}


def test_volume_topup_replays_unfinished_wave(tmp_path: Path, _patch_wver: None) -> None:
    """★ 停在波次中间 ⇒ 重启后**重放该波的对局表**（同 wave_idx ⇒ 同种子流）。

    若只按账本重算，计数器会回到 wave 1，用 wave-1 的种子去补 wave-2 的缺口——
    同观测史、不同波次序列（plan §2.3.1 禁止的「重新抛硬币」）。
    """
    from rl.volume_waves import WAVE_PHASE, wave_round_key

    stub = _StubLoop(tmp_path, target=600000, est=967, samples=500)
    stub._iteration_pairs(1)
    stub._volume_waves = 1
    _settle_first_wave(stub)
    # 造「崩在 w2 中间」的 WAL：w2 有 start、无 finish（对局表 = 每关 36 局）
    games = {0: 36, 1: 36, 2: 36, 3: 36}
    stub._journal.start(
        WAVE_PHASE, wave_round_key(1, 2), games={str(k): v for k, v in games.items()}
    )
    stub._volume_topup(1, None)
    # 重放的一定是 w2 的对局表（不是按账本新算的 w1）
    assert stub.dispatched[0] == wave_pairs(stub._rotate_seed, 1, games, 2)
    # 波次预算不因重启重领：w2 已用过 ⇒ 重放后直接触 wave_cap，不再开新波
    assert stub._volume_waves == 3
    assert len(stub.dispatched) == 1


def test_volume_topup_does_not_replay_finished_wave(tmp_path: Path, _patch_wver: None) -> None:
    """已闭环的波不重放（WAL 只在「停在波中」时才作判据）。"""
    from rl.volume_waves import WAVE_PHASE, wave_round_key

    stub = _StubLoop(tmp_path, target=600000, est=967, samples=500)
    stub._iteration_pairs(1)
    stub._volume_waves = 1
    _settle_first_wave(stub)
    stub._journal.start(WAVE_PHASE, wave_round_key(1, 2), games={"0": 36})
    stub._journal.finish(WAVE_PHASE, wave_round_key(1, 2))
    stub._volume_topup(1, None)
    assert stub.dispatched == []  # 预算已用满（w2 ⇒ waves=3）⇒ 无新波
    assert stub._volume_waves == 3


def test_volume_topup_skips_stream_path(tmp_path: Path, _patch_wver: None) -> None:
    """v1 边界：stream 路径保持老语义（只记日志，不补波）。"""
    stub = _StubLoop(tmp_path, target=600000, est=967, samples=500)
    stub._iteration_pairs(1)
    stub._volume_waves = 1
    _settle_first_wave(stub)
    stub._stream_meta = {"rollout_sec": 1.0}
    stub._volume_topup(1, None)
    assert stub.dispatched == []
    assert stub._volume_collected is None


def test_volume_topup_skips_when_disabled(tmp_path: Path, _patch_wver: None) -> None:
    stub = _StubLoop(tmp_path, target=0)
    stub._volume_topup(1, None)
    assert stub.dispatched == []


def _topup_from_ledger(
    tmp: Path,
    half: list[tuple[int, int]],
    *,
    target: int = 600000,
    est: int = 967,
    samples: int = 500,
) -> _StubLoop:
    """造一个「初波已结算 + 第二波只落了一半」的循环桩，然后跑补波（崩后续跑）。"""
    stub = _StubLoop(tmp, target=target, est=est, samples=samples)
    stub._iteration_pairs(1)
    stub._volume_waves = 1
    _settle_first_wave(stub)
    for stage, seed in half:
        _manifest(stub._traj_dir, stage, seed, _WVER, stub.samples)
    stub._volume_topup(1, None)
    return stub


def _initial(stub: _StubLoop) -> list[tuple[int, int]]:
    """重算「初波 pairs」（_iteration_pairs 的返回值已被消费，此处按同一键重推）。"""
    return wave_pairs(stub._rotate_seed, stub.it, {s: stub._volume_g0 for s in range(4)}, 0)


def _settle_first_wave(stub: _StubLoop) -> None:
    """模拟初波采集结算：shard 落盘 + 报告并入 _report（`dispatched` 只留补波）。"""
    pairs = _initial(stub)
    report = stub._dispatch_volume_wave(stub.it, pairs, None)
    stub.dispatched.clear()
    stub._report = {
        "games": report["games"],
        "winRate": 0.0,
        "outcomes": report["outcomes"],
        "totalSamples": report["totalSamples"],
        "totalTicks": report["totalTicks"],
        "scoreList": [],
        "dimLists": {},
    }

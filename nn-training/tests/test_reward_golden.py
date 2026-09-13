"""test_reward_golden —— 公式引擎 golden 回归 + 安全边界（M1a，plan §4.6 / §11）。

放弃对齐 TS 后（用户 2026-09-02 决策），闸门从「逐位复现旧 TS 奖励」改为三层弱闸：

  1. **golden-file pytest**：固定 counter 向量 + outcome → 期望值入库，Python 逐位复现
  2. **v7 移植保真**：v7 公式 / 内置 `v7_phi` 对 TS oracle（`export-rl-rollout.ts`
     的 v7 势）≤1e-9 —— 由 `tests/golden/v7_phi_ts_oracle.json` 承载（bun 生成，
     一次性；bun 侧不留调 Python 子进程的脆测试，评审 P1-8）
  3. **端到端 shard 级确定性**：见 `test_metrics_shard.py`（M1b）

覆盖向量（评审 R1-5）：「同一 counter 向量 + 不同 outcome → 末样本差异」
「N=1 单样本局」「row 0 基线不产生样本」「wavg 的 v7 公式逐位命中」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.config import CURRICULA_DIR, CourseConfig, load_course
from rl.jsonc import strip_comments
from rl.reward_builtin import V7_DEFAULT_PARAMS, v7_phi
from rl.reward_library import (
    CORE_FUNCS,
    EXT_FUNCS,
    METRIC_INDEX,
    METRICS,
    METRICS_DIM,
    METRICS_VERSION,
    TIME_AXIS_REDUCERS,
    CompiledFormula,
    FormulaError,
    RewardSpec,
    ScheduleSpec,
    build_reward_fn,
    compile_formula,
    parse_formula,
)
from rl.reward_validation import DEFAULT_RANGES, symbolic_envelope, validate_reward

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _metrics(rows: int, **overrides: float) -> np.ndarray:
    """构造 `[rows,21]` 指标矩阵（默认全 0，按名覆盖整列）。"""
    m = np.zeros((rows, METRICS_DIM), dtype=np.float64)
    for k, v in overrides.items():
        m[:, METRIC_INDEX[k]] = v
    return m


# ================================================================== 安全边界


def test_no_time_axis_reducers() -> None:
    """白名单不得包含任何时间轴（跨决策步）归约函数——`wavg` 是特征轴归约的唯一例外。"""
    allf = set(CORE_FUNCS) | set(EXT_FUNCS)
    assert not (allf & TIME_AXIS_REDUCERS), (
        f"白名单混入时间轴归约：{sorted(allf & TIME_AXIS_REDUCERS)}"
    )
    assert "wavg" in CORE_FUNCS
    # min/max 必须是二元逐元素，不是归约
    cf = CompiledFormula(parse_formula("min(kills,1)", {}), False)
    assert np.array_equal(cf.phi(_metrics(3, kills=5), {}), np.full(3, 1.0))
    with pytest.raises(FormulaError, match="参数个数非法"):
        parse_formula("min(kills)", {})  # 一元 = 归约语义，必须拒


@pytest.mark.parametrize(
    "expr",
    [
        "np.load('x.npy')",  # Attribute → 文件系统
        "os.system('rm -rf /')",  # Attribute → OS
        "__import__('os').system('ls')",  # Call + Name + Attribute
        "sum(kills)",  # 时间轴归约
        "mean(kills)",
        "cumsum(kills)",
        "max(kills)",  # 一元 max = 归约
        "kills[0]",  # Subscript
        "(lambda x: x)(kills)",  # Lambda
        "[k for k in kills]",  # 推导式（ListComp 不在白名单）
    ],
)
def test_rejects_dangerous_forms(expr: str) -> None:
    with pytest.raises(FormulaError):
        parse_formula(expr, {})


@pytest.mark.parametrize(
    "expr",
    [
        "kills.__class__",  # Attribute 在指标上
        "'abc'",  # 字符串常量
        "kills + unknownVar",  # 未知符号
        "clip(kills, 0)",  # arity 不足
        "wavg(kills, 1, kills)",  # wavg 奇数参数
        "sin(kills)",  # 扩展层未开启
    ],
)
def test_rejects_malformed(expr: str) -> None:
    with pytest.raises(FormulaError):
        parse_formula(expr, {})


def test_extended_funcs_opt_in() -> None:
    with pytest.raises(FormulaError, match="白名单外的函数"):
        parse_formula("sin(kills)", {})
    cf = compile_formula("sin(kills) + cos(ticks)", {}, allow_extended=True)
    out = cf.phi(_metrics(2, kills=0.0, ticks=0.0), {})
    np.testing.assert_allclose(out, np.array([1.0, 1.0]))  # sin(0)+cos(0) = 0+1


def test_limits_trigger_degrade_card() -> None:
    """降级卡：formula >1024 字符 → 有 builtin 时回退（warning），无 builtin 时硬失败。"""
    long_f = " + ".join(["kills"] * 400)
    assert len(long_f) > 1024
    with pytest.raises(FormulaError, match="降级卡"):
        parse_formula(long_f, {})
    # 声明了 builtin → 回退到内置（不静默：走 log warning 分支）
    spec = RewardSpec(formula=long_f, builtin="v7", params=dict(V7_DEFAULT_PARAMS))
    fn = build_reward_fn(spec)
    assert fn._compiled is None and fn._builtin is not None
    out = fn(_metrics(3, kills=1, enemyTotal=4), "stage_clear", 0.0, 1)
    assert out.shape == (2,) and np.all(np.isfinite(out))


def test_degrade_only_on_quantitative_triggers() -> None:
    """评审 F1：降级卡**只**在量化触发面（>1024 字符 / AST>64）回退 builtin——

    语法错误 / 未知名 params / 白名单外符号是配置错误，声明了 builtin 也必须
    响亮 raise（否则调参手误 → 实验静默换内置，golden 在 v7 域上还不暴露）。
    """
    import re as _re

    # 量化触发面 → 回退（warning 带真实原因）
    long_f = " + ".join(["kills"] * 400)
    spec = RewardSpec(formula=long_f, builtin="v7", params=dict(V7_DEFAULT_PARAMS))
    fn = build_reward_fn(spec)
    assert fn._compiled is None and fn._builtin is not None

    # 语法错误 + builtin → 硬失败（不回退）
    with pytest.raises(FormulaError, match="语法错误"):
        build_reward_fn(RewardSpec(formula="kills +", builtin="v7", params={}))
    # 未知名 params 拼错（wP→wq 实测案例）+ builtin → 硬失败
    with pytest.raises(FormulaError, match="未知符号 'wq'"):
        build_reward_fn(RewardSpec(formula="wq*kills + 1", builtin="v7", params={"wP": 0.3}))
    # 白名单外函数 + builtin → 硬失败（需 allow_extended_funcs 显式开启）
    with pytest.raises(FormulaError, match="白名单外的函数"):
        build_reward_fn(RewardSpec(formula="tanh(kills)", builtin="v7", params={}))
    # validate_reward 侧同语义：配置错误记 errors（不回退 warning）
    rep = validate_reward(RewardSpec(formula="wq*kills + 1", builtin="v7", params={"wP": 0.3}))
    assert not rep.ok and any("配置错误" in e for e in rep.errors), rep.errors
    # 量化触发面 validate 侧：仍回退（warning）
    rep2 = validate_reward(RewardSpec(formula=long_f, builtin="v7", params=dict(V7_DEFAULT_PARAMS)))
    assert rep2.ok and any("降级卡" in w for w in rep2.warnings), rep2.warnings


def test_envelope_extended_funcs_no_false_positive() -> None:
    """评审 F2：symbolic_envelope 对扩展层公式不再误报（allow_extended 透传）。

    修复前 envelope 的子 compile 不透传 allow_extended_funcs → tanh/sin 等扩展
    函数每个项都标 inf「数值包络超限」污染启动日志。有界扩展函数现在应干净通过。
    """
    spec = RewardSpec(
        formula="tanh(kills) + 0.5*sin(ticks/1000)", params={}, allow_extended_funcs=True
    )
    rep = validate_reward(spec)
    assert rep.ok, rep.errors
    assert not any("数值包络超限" in w for w in rep.warnings), rep.warnings
    # 关闭扩展层 → 白名单外函数 → 配置错误（硬失败），而不是假 inf 警告
    spec_off = RewardSpec(formula="tanh(kills)", params={})
    rep_off = validate_reward(spec_off)
    assert not rep_off.ok and any("配置错误" in e for e in rep_off.errors), rep_off


def test_isfinite_guard() -> None:
    """除零/溢出不得静默污染 GAE —— Φ 非有限即报错。"""
    cf = compile_formula("kills/playerShots", {})
    with pytest.raises(FormulaError, match="非有限值"):
        cf.phi(_metrics(2, kills=1.0, playerShots=0.0), {})


# ================================================================== wrapper 骨架


def test_row0_is_baseline_not_sample() -> None:
    """row 0 是基线，不产生样本：reward 长度 = N（行数 − 1）。"""
    spec = RewardSpec(formula="kills", params={}, terminal={"stage_clear": 1.0})
    fn = build_reward_fn(spec)
    m = np.zeros((5, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [0, 1, 2, 3, 4]  # 4 个决策快照 + 1 终局快照
    r = fn(m, "timeout", 0.0, 1)
    assert r.shape == (4,)
    np.testing.assert_allclose(r, np.ones(4))


def test_single_sample_episode_n1() -> None:
    """N=1 单样本局：2 行 metrics → 1 个样本 = 势差 + terminal。"""
    spec = RewardSpec(formula="2*kills", params={}, terminal={"stage_clear": 5.0})
    fn = build_reward_fn(spec)
    m = np.zeros((2, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [0, 3]
    r = fn(m, "stage_clear", 0.0, 1)
    assert r.shape == (1,)
    assert r[0] == pytest.approx(6.0 + 5.0)


def test_outcome_changes_only_last_sample() -> None:
    """同一 counter 向量 + 不同 outcome → 只有末样本变（前 N-1 个逐位相同）。"""
    spec = RewardSpec(
        formula="kills", params={}, terminal={"stage_clear": 2.0, "lives_exhausted": -1.5}
    )
    fn = build_reward_fn(spec)
    m = np.zeros((6, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [0, 1, 1, 2, 2, 2]
    r_clear = fn(m, "stage_clear", 0.0, 1)
    r_dead = fn(m, "lives_exhausted", 0.0, 1)
    np.testing.assert_array_equal(r_clear[:-1], r_dead[:-1])
    assert r_clear[-1] - r_dead[-1] == pytest.approx(2.0 - (-1.5))
    # 未列出的 outcome（timeout/base_destroyed）= 0 → 只有末样本少掉 -1.5
    r_to = fn(m, "timeout", 0.0, 1)
    np.testing.assert_array_equal(r_to[:-1], r_dead[:-1])
    assert r_to[-1] - r_dead[-1] == pytest.approx(1.5)
    np.testing.assert_array_equal(fn(m, "base_destroyed", 0.0, 1), r_to)


def test_score_reconcile_telescoping() -> None:
    """score_reconcile：Σr ≡ scale × gatedScore（与 outcome/势路径无关）。"""
    spec = RewardSpec(
        formula="3*kills - ticks/1000", params={}, scheme="score_reconcile", reward_scale=10.0
    )
    fn = build_reward_fn(spec)
    rng = np.random.default_rng(11)
    m = np.zeros((50, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = np.cumsum(rng.integers(0, 2, 50))
    m[:, METRIC_INDEX["ticks"]] = np.arange(50) * 10
    for score in (0.0, 0.21, 0.83, 1.0):
        for outcome in ("stage_clear", "base_destroyed"):
            r = fn(m, outcome, score, 1)
            assert r.sum() == pytest.approx(10.0 * score, abs=1e-9), (score, outcome)


def test_param_schedule_linear_and_step() -> None:
    sch_lin = ScheduleSpec(fro=0.0, to=0.2, until_iter=20, mode="linear")
    assert sch_lin.value_at(1) == pytest.approx(0.0)
    assert sch_lin.value_at(20) == pytest.approx(0.2)
    assert sch_lin.value_at(50) == pytest.approx(0.2)  # 窗口后停在 to
    assert 0.0 < sch_lin.value_at(10) < 0.2
    sch_step = ScheduleSpec(fro=0.0, to=0.2, until_iter=20, mode="step")
    assert sch_step.value_at(19) == 0.0 and sch_step.value_at(20) == 0.2
    with pytest.raises(FormulaError, match="未知"):
        ScheduleSpec.from_dict("k", {"from": 0, "to": 1, "until_iter": 5, "mode": "cosine"})
    # 每 iter 折算后的实际权重进入 Φ
    spec = RewardSpec(
        formula="w*kills", params={"w": 0.0}, param_schedule={"w": sch_lin}, terminal={}
    )
    fn = build_reward_fn(spec)
    m = np.zeros((2, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [0, 1]
    assert fn(m, "timeout", 0.0, 1)[0] == pytest.approx(0.0)
    assert fn(m, "timeout", 0.0, 20)[0] == pytest.approx(0.2)


# ================================================================== v7


def test_v7_formula_matches_builtin_bitwise() -> None:
    """v7 配置公式 与 内置 `v7_phi`（TS 移植）在同一 params 下逐位一致。"""
    course = load_course("s4b")
    spec = course.reward_spec()
    fn = build_reward_fn(spec)
    assert fn._compiled is not None, "v7 公式应能编译（607 字符 < 1024 限长）"
    m = _v7_corpus(n=2000, seed=3)
    a = fn.phi(m, 1)
    b = v7_phi(m, spec.params)
    np.testing.assert_array_equal(a, b)  # 逐位（不是 allclose）


def test_v7_first_kill_sentinel() -> None:
    """firstKillTick=-1（无首杀）→ openingTempo=0，但权重仍计入分母（评审 P0-7）。"""
    course = load_course("s4b")
    spec = course.reward_spec()
    fn = build_reward_fn(spec)
    base = dict(
        ticks=3600.0,
        kills=4.0,
        lives=3.0,
        playerShots=40.0,
        powerUpsSpawned=4.0,
        powerUpsCollected=2.0,
        baseAlive=1.0,
        baseWallTotal=8.0,
        baseWallIntact=8.0,
        basePressureSum=100.0,
        basePressureSamples=600.0,
        enemyTotal=20.0,
    )
    m_no = _metrics(1, firstKillTick=-1.0, **base)
    m_early = _metrics(1, firstKillTick=0.0, **base)
    m_mid = _metrics(1, firstKillTick=900.0, **base)  # ramp(900,0,1800)=0.5 → tempo 0.5
    m_late = _metrics(1, firstKillTick=3600.0, **base)  # 已过 1800 宽限 → tempo=0
    phi_no = float(fn.phi(m_no, 1)[0])
    phi_early = float(fn.phi(m_early, 1)[0])
    phi_mid = float(fn.phi(m_mid, 1)[0])
    phi_late = float(fn.phi(m_late, 1)[0])
    assert phi_early > phi_mid > phi_no  # 早杀 > 半程杀 > 无杀
    # 宽限窗外的击杀不再给 tempo 信用：与无首杀同值（但其余维度同态）
    assert phi_late == pytest.approx(phi_no, abs=1e-12)
    assert phi_no > 0.0  # 权重仍计分母 ⇒ 其余维度仍贡献


def test_v7_base_destroyed_gate() -> None:
    """F3 门控：base_destroyed → 势 × M（0.1）。"""
    course = load_course("s4b")
    fn = build_reward_fn(course.reward_spec())
    base = dict(
        ticks=3600.0,
        kills=5.0,
        lives=2.0,
        playerShots=50.0,
        powerUpsSpawned=4.0,
        powerUpsCollected=2.0,
        baseWallTotal=8.0,
        baseWallIntact=0.0,
        basePressureSum=300.0,
        basePressureSamples=600.0,
        enemyTotal=20.0,
        firstKillTick=100.0,
    )
    alive = float(fn.phi(_metrics(1, baseAlive=1.0, **base), 1)[0])
    dead = float(fn.phi(_metrics(1, baseAlive=0.0, **base), 1)[0])
    assert dead < alive * 0.5  # 门控把势压到远低于存活局


# ================================================================== JSONC


def test_strip_comments() -> None:
    assert strip_comments('{"a": 1} // tail') == '{"a": 1} '
    assert strip_comments('{"a": "//not a comment"}') == '{"a": "//not a comment"}'
    # 转义引号后的 // 仍在字符串内
    assert strip_comments(r'{"a": "x\"//y"}') == r'{"a": "x\"//y"}'
    # 换行保留（行号不漂移 → json 报错定位准确）
    assert strip_comments('{\n// c\n"a":1}') == '{\n\n"a":1}'


def test_jsonc_trailing_commas_and_loads() -> None:
    """JSONC 加载器：注释 + 尾逗号（`, }`/`, ]`）双容忍；字符串内不受影响。"""
    from rl.jsonc import loads as jsonc_loads

    d = jsonc_loads('{\n  "a": [1, 2,],  // 尾逗号+注释\n  "b": {"x": 1,},\n}')
    assert d == {"a": [1, 2], "b": {"x": 1}}
    # 字符串内的 `},` 原样保留
    assert jsonc_loads('{"s": "a,},b"}') == {"s": "a,},b"}


def test_jsonc_courses_load() -> None:
    files = sorted(CURRICULA_DIR.glob("*.jsonc"))
    assert len(files) >= 5, f"课程配置太少：{files}"
    for f in files:
        c = load_course(f)
        assert isinstance(c, CourseConfig)
        assert c.name


# ================================================================== 课程可用性


@pytest.mark.parametrize(
    "name", ["s1", "s2", "s3-balanced", "s-dodge", "s4b", "_example-custom-stage"]
)
def test_course_formula_expressible(name: str) -> None:
    """「改配置不改代码」成立：每段公式都能编译、能在随机 metrics 上出有限 reward。"""
    course = load_course(name)
    spec = course.reward_spec()
    rep = validate_reward(spec)
    assert rep.ok, rep.errors
    fn = build_reward_fn(spec)
    m = _v7_corpus(n=64, seed=5)
    for outcome in ("stage_clear", "lives_exhausted", "timeout", "base_destroyed"):
        r = fn(m, outcome, 0.42, 1)
        assert r.shape == (63,) and np.all(np.isfinite(r))


def test_course_stage_mapping() -> None:
    """自定义关第 i 个 → stage ID 2000+i；字符串规格直接透传。"""
    c = load_course("_example-custom-stage")
    assert c.is_custom_stages and c.stage_ids == [2000, 2001]
    assert c.stages_range() == "2000,2001"
    sj = c.stage_json(2000)
    assert sj and len(sj.encode()) <= 4096
    payload = json.loads(sj)
    assert len(payload["grid"]) == 13 and payload["grid"][0].__len__() == 13
    assert payload["count"] == 20
    assert c.stage_json(1999) is None
    # 真实关课程
    s4b = load_course("s4b")
    assert not s4b.is_custom_stages and s4b.stage_ids == list(range(35))
    assert s4b.stage_json(0) is None


def test_symb_envelope_flags_explosion() -> None:
    """symbolic_envelope 在启动期拦住数值爆炸（warn，不硬崩）。"""
    ok = validate_reward(RewardSpec(formula="0.01*kills + 0.001*ticks", params={}))
    assert ok.ok and not ok.warnings, ok.warnings
    boom = validate_reward(RewardSpec(formula="exp(kills)", params={}))
    assert boom.ok  # 尺度问题 = warn-only（评审 P1-4）
    assert any("数值包络超限" in w for w in boom.warnings), boom.warnings
    # 逐项结果可直接读出峰值
    terms = symbolic_envelope(parse_formula("exp(kills) + 2*ticks", {}), {})
    assert {t.vars for t in terms} == {("kills",), ("ticks",)}


# ================================================================== golden 文件


def _v7_corpus(n: int, seed: int) -> np.ndarray:
    """随机但覆盖 null/哨兵分支的指标矩阵（v7 对账 + 课程可用性共用）。"""
    rng = np.random.default_rng(seed)
    m = np.zeros((n, METRICS_DIM), dtype=np.float64)
    m[:, METRIC_INDEX["ticks"]] = rng.integers(0, 36000, n)
    m[:, METRIC_INDEX["kills"]] = rng.integers(0, 25, n)
    m[:, METRIC_INDEX["lives"]] = rng.integers(0, 4, n)
    m[:, METRIC_INDEX["playerShots"]] = rng.integers(0, 300, n)
    m[:, METRIC_INDEX["powerUpsCollected"]] = rng.integers(0, 10, n)
    m[:, METRIC_INDEX["powerUpsSpawned"]] = rng.integers(0, 12, n)
    m[:, METRIC_INDEX["baseAlive"]] = rng.integers(0, 2, n)
    m[:, METRIC_INDEX["baseWallTotal"]] = rng.integers(0, 9, n)
    m[:, METRIC_INDEX["baseWallIntact"]] = rng.integers(0, 9, n)
    m[:, METRIC_INDEX["basePressureSum"]] = rng.random(n) * 5000
    m[:, METRIC_INDEX["basePressureSamples"]] = rng.integers(0, 6000, n)
    m[:, METRIC_INDEX["firstKillTick"]] = rng.choice([-1.0, 0.0, 500.0, 2000.0, 36000.0], n)
    m[:, METRIC_INDEX["enemyTotal"]] = rng.integers(1, 21, n)
    # clearTick 显式写哨兵 -1（= 本局未清场）。此前留 np.zeros 的默认 0.0 —— 而 0.0 是
    # **合法值**（"第 0 tick 就已清场"），会被任何读该列的公式当成"已清场"：这里写的
    # 是「未清场」的诚实取值（2026-09-12）。清场分支由 `_clear_metrics` 的行序列覆盖。
    m[:, METRIC_INDEX["clearTick"]] = -1.0
    return m


def _clear_metrics(ticks: list[float], clear_from: int | None) -> np.ndarray:
    """单调 tick 序列 + 「自 `clear_from` 行起 clearTick 恒为该行 tick」的指标矩阵。

    `clear_from=None` ⇒ 全程 `clearTick=-1`（未清场）。真实语料里 clearTick 是**每行同值
    的标量**（一旦清场就固定），所以只有这种"前段 -1、后段恒 C"的形状才能同时触发
    "清场前按 ticks 罚 / 清场后按 C+wBonusTicks 封顶"两个分支。
    """
    m = np.zeros((len(ticks), METRICS_DIM), dtype=np.float64)
    m[:, METRIC_INDEX["ticks"]] = ticks
    m[:, METRIC_INDEX["clearTick"]] = -1.0
    if clear_from is not None:
        m[clear_from:, METRIC_INDEX["clearTick"]] = float(ticks[clear_from])
    return m


#: c6-bonus 专属行序列（2026-09-12 补）：tick 0→3000 步长 600，清场于第 1 行（tick=600）
#: ⇒ 窗口上界 clearTick+wBonusTicks = 1200，正好落在第 2 行的 tick 上（边界可读）。
_C6_TICKS = [0.0, 600.0, 1200.0, 1800.0, 2400.0, 3000.0]


def _golden_vectors() -> list[dict]:
    """固定 counter 向量 + outcome 组合（golden 回归的输入；**不得随意改动**——
    改动 = 奖励语义变化，需重生成 golden 并在 DECISIONS 记录）。

    语料两类：
      1. `_v7_corpus` 随机行 × 5 门课 × 4 outcome × 3 it（含 s1/s2/s3-balanced/s-dodge/s4b）；
      2. **c6-bonus 专属手写序列**（下方）：`_v7_corpus` 的随机行 tick 非单调、clearTick
         恒 -1，测不到 `wClear*(is_timeout and clearTick>=0)`（常数项 diff 恒 0）与
         `wBonusTicks` 封顶 —— 故用单调行序列补上真实覆盖。
    """
    rng = np.random.default_rng(20260902)
    m = _v7_corpus(n=32, seed=20260902)
    cases = [
        {
            "course": name,
            "outcome": outcome,
            "gated": float(round(rng.random(), 4)),
            "it": it,
            "metrics": m.tolist(),
        }
        for name in ("s1", "s2", "s3-balanced", "s-dodge", "s4b")
        for outcome in ("stage_clear", "lives_exhausted", "timeout", "base_destroyed")
        for it in (1, 7, 20)
    ]
    # c6-bonus：清场（中途置位）/ 未清场 两种行序列 × 各自现实的 outcome 组合
    for label, clear_from, outcomes in (
        ("cleared", 1, ("timeout", "stage_clear")),
        ("uncleared", None, ("timeout", "lives_exhausted")),
    ):
        matrix = _clear_metrics(_C6_TICKS, clear_from)
        for outcome in outcomes:
            cases.append(
                {
                    "course": "c6-bonus",
                    "outcome": outcome,
                    "gated": 0.0,
                    "it": 1,
                    "metrics": matrix.tolist(),
                    "note": f"clearTick={label}",
                }
            )
    return cases


def test_c6_bonus_clear_compensation() -> None:
    """清场补偿与 `wBonusTicks` 豁免边界（c6-bonus，2026-09-12）。

    语义（`c6-bonus.jsonc`）：
      Φ = … − wTick·(min(ticks, clearTick+wBonusTicks) if clearTick ≥ 0 else ticks)
             + wClear·[is_timeout ∧ clearTick ≥ 0]
    期望值这里**手算**出来（wTick=0.01, wBonusTicks=600, wClear=4.0, terminal
    stage_clear=+2 / timeout=−2；r = diff(Φ) 且终局额挂最后一步），不从引擎反推。
    """
    fn = build_reward_fn(load_course("c6-bonus").reward_spec())
    cleared = _clear_metrics(_C6_TICKS, 1)  # 清场于 tick=600 ⇒ 封顶上界 1200
    uncleared = _clear_metrics(_C6_TICKS, None)

    r_clear_to = fn(cleared, "timeout", 0.0, 1)
    r_clear_sc = fn(cleared, "stage_clear", 0.0, 1)
    r_no_to = fn(uncleared, "timeout", 0.0, 1)

    # 清场+被截断：Φ=[0,−2,−8,−8,−8,−8]（第 1 步含 +4 补偿，其后 tick 罚封顶）
    np.testing.assert_allclose(r_clear_to, [-2.0, -6.0, 0.0, 0.0, -2.0], atol=1e-12)
    # 正常过关：同样封顶但不拿 +4（只拿 terminal +2）⇒ **两者总额相等**
    np.testing.assert_allclose(r_clear_sc, [-6.0, -6.0, 0.0, 0.0, 2.0], atol=1e-12)
    assert r_clear_to.sum() == pytest.approx(r_clear_sc.sum())
    # 未清场：全额计时 + 无补偿（比清场局差得多）
    np.testing.assert_allclose(r_no_to, [-6.0, -6.0, -6.0, -6.0, -8.0], atol=1e-12)
    assert r_no_to.sum() < r_clear_to.sum()

    # 边界：豁免止于 clearTick + wBonusTicks（**含端点**）—— 1199→1200 还计费、
    # 1200→1201 之后不再计费。
    boundary = fn(_clear_metrics([600.0, 1199.0, 1200.0, 1201.0, 1300.0], 0), "timeout", 0.0, 1)
    np.testing.assert_allclose(boundary, [-5.99, -0.01, 0.0, -2.0], atol=1e-12)


def test_golden_file() -> None:
    """golden-file 回归：入库的期望值与当前实现逐位一致。"""
    path = GOLDEN_DIR / "reward_golden.json"
    if not path.exists():
        pytest.skip(f"golden 文件不存在（用 scripts/regen_reward_golden.py 生成）：{path}")
    golden = json.loads(path.read_text(encoding="utf-8"))
    # 版本号读 SSOT（`reward_library.METRICS_VERSION`），不再硬编码 —— 2026-09-12 修：
    # 此前写死 4，metrics v5 重生成后 golden 内容正确却被该断言误判为"需重新生成"。
    assert golden.get("metrics_version") == METRICS_VERSION, (
        "golden 与指标向量版本不匹配——需重新生成"
    )
    cache: dict[str, Any] = {}
    for case in golden["cases"]:
        name = case["course"]
        if name not in cache:
            cache[name] = build_reward_fn(load_course(name).reward_spec())
        fn = cache[name]  # type: ignore[index]
        got = fn(
            np.asarray(case["metrics"], dtype=np.float64),
            case["outcome"],
            case["gated"],
            case["it"],
        )
        np.testing.assert_array_equal(
            got,
            np.asarray(case["reward"], dtype=np.float64),
            err_msg=f"golden 失配：{name}/{case['outcome']}/it{case['it']}",
        )


def test_v7_ts_oracle_fidelity() -> None:
    """v7 对 TS oracle 的保真度 ≤1e-9（`export-rl-rollout.ts` 的 v7 势）。

    golden 由 `bun tools/diag/v7-phi-oracle.ts` 一次性生成，bun 侧不留调 Python
    子进程的脆测试（评审 P1-8）。文件缺失即 skip（不阻塞 CI）。
    """
    path = GOLDEN_DIR / "v7_phi_ts_oracle.json"
    if not path.exists():
        pytest.skip(f"TS oracle golden 不存在（bun tools/diag/v7-phi-oracle.ts 生成）：{path}")
    oracle = json.loads(path.read_text(encoding="utf-8"))
    spec = load_course("s4b").reward_spec()
    fn = build_reward_fn(spec)
    m = np.asarray(oracle["metrics"], dtype=np.float64)
    got = fn.phi(m, 1)
    exp = np.asarray(oracle["phi"], dtype=np.float64)
    assert np.max(np.abs(got - exp)) <= 1e-9, (
        f"v7 公式偏离 TS oracle：{np.max(np.abs(got - exp)):.3e}"
    )
    # 内置实现同样要对上
    assert np.max(np.abs(v7_phi(m, spec.params) - exp)) <= 1e-9


# ================================================================== 终局重分配（§382）


def _toy_pair() -> tuple[Any, Any]:
    """同一 spec 的 lump 版 / spread 版 RewardFn 对（仅 terminal_spread 不同）。"""
    base: dict[str, Any] = dict(
        formula="3*kills + wHit*enemyHits",
        params={"wHit": 0.3},
        terminal={"stage_clear": 2.0, "lives_exhausted": -1.0, "timeout": -2.0},
    )
    return build_reward_fn(RewardSpec(**base)), build_reward_fn(
        RewardSpec(**base, terminal_spread=True)
    )


def test_spread_sum_identity_toy() -> None:
    """spread 版 Σr 与 lump 版逐位同（四种 outcome 全测）——只换时间分配。"""
    lump, spread = _toy_pair()
    rng = np.random.default_rng(382)
    m = np.zeros((40, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = np.cumsum(rng.integers(0, 2, 40))
    m[:, METRIC_INDEX["enemyHits"]] = np.cumsum(rng.integers(0, 3, 40))
    for outcome in ("stage_clear", "lives_exhausted", "timeout", "base_destroyed"):
        assert lump(m, outcome, 0.0, 1).sum() == pytest.approx(
            spread(m, outcome, 0.0, 1).sum(), abs=1e-12
        ), outcome


def test_spread_uniform_values() -> None:
    """spread 版每步 = 势差 + T/N；outcome 差异摊到全序列（非仅末样本）。"""
    lump, spread = _toy_pair()
    m = np.zeros((6, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [0, 1, 1, 2, 2, 2]  # N=5
    r_spread = spread(m, "timeout", 0.0, 1)
    dense = np.diff([0, 3, 3, 6, 6, 6])  # Φ=kills*3 的势差
    np.testing.assert_allclose(r_spread, dense - 2.0 / 5)
    # outcome 差 spread 到每一步：clear 与 timeout 全序列差恒定
    d = spread(m, "stage_clear", 0.0, 1) - spread(m, "timeout", 0.0, 1)
    np.testing.assert_allclose(d, np.full(5, (2.0 - (-2.0)) / 5))


def test_spread_n1_equals_lump() -> None:
    """N=1 单样本局：均摊退化为 lump（逐位同）。"""
    lump, spread = _toy_pair()
    m = np.zeros((2, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [1, 4]
    for outcome in ("stage_clear", "timeout"):
        np.testing.assert_array_equal(
            spread(m, outcome, 0.0, 1), lump(m, outcome, 0.0, 1)
        )


def test_spread_reconcile_sum_identity() -> None:
    """reconcile 版 spread：Σr 仍 ≡ scale×gatedScore。"""
    base: dict[str, Any] = dict(
        formula="3*kills - ticks/1000", params={}, scheme="score_reconcile",
        reward_scale=10.0,
    )
    fn = build_reward_fn(RewardSpec(**base, terminal_spread=True))
    rng = np.random.default_rng(382)
    m = np.zeros((50, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = np.cumsum(rng.integers(0, 2, 50))
    m[:, METRIC_INDEX["ticks"]] = np.arange(50) * 10
    for score in (0.0, 0.21, 0.83, 1.0):
        assert fn(m, "timeout", score, 1).sum() == pytest.approx(
            10.0 * score, abs=1e-9
        ), score


def test_spread_default_off_and_identity() -> None:
    """默认关闭（历史行为）：flag 缺省 False；开/关指纹不同（D14 隔离生效）。"""
    lump, spread = _toy_pair()
    assert lump.spec.terminal_spread is False
    assert spread.spec.terminal_spread is True
    assert lump.spec.identity() != spread.spec.identity()
    # 关 = 旧语义：outcome 只动末样本（与既有 golden 同断言形状）
    m = np.zeros((6, METRICS_DIM))
    m[:, METRIC_INDEX["kills"]] = [0, 1, 1, 2, 2, 2]
    r_clear = lump(m, "stage_clear", 0.0, 1)
    r_dead = lump(m, "lives_exhausted", 0.0, 1)
    np.testing.assert_array_equal(r_clear[:-1], r_dead[:-1])


def test_spread_course_plumbing() -> None:
    """课程 plumbing：vk1 关 / rd1 开（RewardBlock→RewardSpec 透传）。"""
    assert load_course("p3-vk1").reward_spec().terminal_spread is False
    assert load_course("p3-rd1").reward_spec().terminal_spread is True


# ================================================================== 道具流指标 v3（§9）


def test_item_metrics_layout_locked() -> None:
    """v3/v4/v5 锁步：0–20 列号永久不动，新列一律追加在尾部（TS metricsRow 同序）。

    尾部清单是**穷举**断言 ⇒ 每次加列都必须来这里登记（v5 加 `clearTick` 时漏登记过，
    这正是本测试存在的意义）。
    """
    from rl.reward_library import METRIC_INDEX, METRICS

    assert METRICS[20] == "enemyTotal"
    assert list(METRICS[21:]) == [
        "puSpawnBomb",
        "puSpawnTank",
        "puSpawnFreeze",
        "puSpawnShield",
        "puGotBomb",
        "puGotTank",
        "puGotFreeze",
        "puGotShield",
        "puSpawnStar",
        "clearTick",  # idx30（v5：敌人首次全灭的 tick，哨兵 -1）
    ]
    assert METRIC_INDEX["puGotBomb"] == 25
    assert METRIC_INDEX["puSpawnShield"] == 24
    assert METRIC_INDEX["puSpawnStar"] == 29
    assert METRIC_INDEX["clearTick"] == 30


def test_item_metrics_formula_and_envelope() -> None:
    """新列可用：公式引用求值正确 + validation 包络放行。"""
    spec = RewardSpec(
        formula="wKill*kills + wBomb*puGotBomb - wDmg*playerHits",
        params={"wKill": 3.0, "wBomb": 2.0, "wDmg": 1.0},
        terminal={"timeout": -2.0},
    )
    rep = validate_reward(spec)
    assert rep.ok, rep.errors
    fn = build_reward_fn(spec)
    m = _metrics(5, kills=2, enemyHits=3)
    m[:, METRIC_INDEX["puGotBomb"]] = [0, 0, 1, 1, 1]
    r = fn(m, "timeout", 0.0, 1)
    assert r.shape == (4,) and np.all(np.isfinite(r))
    # bomb 到手那步多 +wBomb（势差含 2.0）
    dense = np.diff(3.0 * np.array([2, 2, 2, 2, 2])) + np.diff(
        2.0 * np.array([0, 0, 1, 1, 1])
    )
    np.testing.assert_allclose(r[:-1], dense[:-1])
    assert r[-1] == pytest.approx(dense[-1] - 2.0)


def test_all_metrics_have_envelope_range() -> None:
    """每个指标列都必须登记包络取值域（2026-09-12 v5 回归）。

    `symbolic_envelope` 逐项枚举指标角点，缺取值域会让 `validate_reward(course)`
    直接崩（当时是裸 KeyError 穿过异常分支）——加列必须同步登记，这里就拦住。
    """
    assert set(METRICS) == set(DEFAULT_RANGES), (
        f"未登记取值域的指标列：{sorted(set(METRICS) - set(DEFAULT_RANGES))}"
    )


def test_envelope_handles_outcome_virtual_terms() -> None:
    """引用 outcome 虚拟符号的加性项不得被包络误判为数值爆炸（2026-09-12 回归）。

    成因：`phi` 在公式引用虚拟符号时**必须**收到 outcome，而 `symbolic_envelope`
    原先不带 outcome 调它 ⇒ 抛 FormulaError ⇒ 被当成除零/溢出记成 inf/超限。
    """
    spec = RewardSpec(
        formula=(
            "wKill*kills + wClear*(1.0 if (is_timeout and clearTick >= 0) else 0.0)"
        ),
        params={"wKill": 3.0, "wClear": 4.0},
        terminal={"timeout": -2.0},
    )
    rep = validate_reward(spec)
    assert rep.ok, rep.errors
    assert rep.warnings == (), rep.warnings
    term = next(t for t in rep.envelope if "wClear" in t.src)
    # 峰值 = wClear（只在 timeout+已清场成立），既没漏算也没爆
    assert term.ok
    assert term.max_abs == pytest.approx(4.0)


def test_envelope_virtual_term_evaluates_zero_outside_outcome() -> None:
    """非 timeout 的结局在包络里取 0（不被算成负值/爆值）。"""
    spec = RewardSpec(
        formula="wClear*(1.0 if (is_timeout and clearTick >= 0) else 0.0)",
        params={"wClear": 4.0},
        terminal={"timeout": -2.0},
    )
    rep = validate_reward(spec)
    assert rep.ok, rep.errors
    (term,) = rep.envelope
    assert term.max_abs == pytest.approx(4.0)
    # 角点上的 clearTick 取上界（36000 ≥ 0）⇒ 成立分支被枚举到
    assert term.vars == ("clearTick",)
    assert term.at == (36000.0,)


if __name__ == "__main__":
    for fn in (
        test_no_time_axis_reducers,
        test_row0_is_baseline_not_sample,
        test_single_sample_episode_n1,
        test_outcome_changes_only_last_sample,
        test_score_reconcile_telescoping,
        test_spread_sum_identity_toy,
        test_spread_uniform_values,
        test_spread_n1_equals_lump,
        test_spread_reconcile_sum_identity,
        test_spread_default_off_and_identity,
        test_spread_course_plumbing,
        test_item_metrics_layout_locked,
        test_item_metrics_formula_and_envelope,
        test_all_metrics_have_envelope_range,
        test_envelope_handles_outcome_virtual_terms,
        test_envelope_virtual_term_evaluates_zero_outside_outcome,
        test_c6_bonus_clear_compensation,
        test_param_schedule_linear_and_step,
        test_v7_formula_matches_builtin_bitwise,
        test_v7_first_kill_sentinel,
        test_v7_base_destroyed_gate,
        test_strip_comments,
        test_jsonc_courses_load,
        test_symb_envelope_flags_explosion,
        test_course_stage_mapping,
    ):
        fn()
        print(f"ok  {fn.__name__}")
    print(f"metrics({METRICS_DIM}): {', '.join(METRICS)}")

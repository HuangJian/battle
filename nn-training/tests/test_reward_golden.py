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
    TIME_AXIS_REDUCERS,
    CompiledFormula,
    FormulaError,
    RewardSpec,
    ScheduleSpec,
    build_reward_fn,
    compile_formula,
    parse_formula,
)
from rl.reward_validation import symbolic_envelope, validate_reward

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
    return m


def _golden_vectors() -> list[dict]:
    """固定 counter 向量 + outcome 组合（golden 回归的输入；**不得随意改动**——
    改动 = 奖励语义变化，需重生成 golden 并在 DECISIONS 记录）。"""
    rng = np.random.default_rng(20260902)
    m = _v7_corpus(n=32, seed=20260902)
    return [
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


def test_golden_file() -> None:
    """golden-file 回归：入库的期望值与当前实现逐位一致。"""
    path = GOLDEN_DIR / "reward_golden.json"
    if not path.exists():
        pytest.skip(f"golden 文件不存在（用 scripts/regen_reward_golden.py 生成）：{path}")
    golden = json.loads(path.read_text(encoding="utf-8"))
    assert golden.get("metrics_version") == 2, "golden 与指标向量版本不匹配——需重新生成"
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


if __name__ == "__main__":
    for fn in (
        test_no_time_axis_reducers,
        test_row0_is_baseline_not_sample,
        test_single_sample_episode_n1,
        test_outcome_changes_only_last_sample,
        test_score_reconcile_telescoping,
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

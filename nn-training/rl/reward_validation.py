"""reward_validation —— 奖励公式的静态校验 + 数值包络（plan §4.7.3）。

三层归一化校验：
  ③ 势塑形合法性 —— 静态 AST 白名单 + 变量域（`parse_formula` 已覆盖，此模块聚合）
  ① 尺度        —— `symbolic_envelope`：逐加性项角点求值，拦 `exp(kills)` 这类数值爆炸
  ② 语义标定    —— 由 metrics_stats 实测，不在此模块

`fail_on_violation` **默认 warn-only**（评审 P1-4）：训练中途分布漂移硬崩 12h run
是事故配方；硬失败只留给启动期的静态校验与包络检查。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from rl.reward_library import (
    METRIC_INDEX,
    METRICS,
    CompiledFormula,
    FormulaDegradeError,
    FormulaError,
    ParsedFormula,
    RewardSpec,
    compile_formula,
)

#: 指标取值域（包络角点用；保守上界，非硬约束）。
#: 来源：maxTicks 36000 / 敌人总数 ≤20(默认) / 26×26=676 格 / 承压每 6 tick 采样。
DEFAULT_RANGES: dict[str, tuple[float, float]] = {
    "ticks": (0.0, 36000.0),
    "kills": (0.0, 40.0),
    "lives": (0.0, 9.0),
    "playerHits": (0.0, 200.0),
    "playerDamageTaken": (0.0, 500.0),
    "playerShots": (0.0, 3000.0),
    "enemyHits": (0.0, 500.0),
    "powerUpsCollected": (0.0, 30.0),
    "powerUpsSpawned": (0.0, 30.0),
    "stuckTicks": (0.0, 36000.0),
    "starsCollected": (0.0, 30.0),
    "baseAlive": (0.0, 1.0),
    "baseWallTotal": (0.0, 8.0),
    "baseWallIntact": (0.0, 8.0),
    "basePressureSum": (0.0, 20000.0),
    "basePressureSamples": (0.0, 6000.0),
    "firstKillTick": (-1.0, 36000.0),
    "playerDeaths": (0.0, 9.0),
    "cellsVisited": (0.0, 676.0),
    "playerLevel": (0.0, 4.0),
    "enemyTotal": (0.0, 40.0),
}

#: 单加性项在角点上的绝对上界（超过即判为数值爆炸风险）。
DEFAULT_ENVELOPE_LIMIT = 1e6

#: 包络枚举的单个加性项最多允许引用几个指标变量（2^n 组合，>4 即 16^n 太贵）。
MAX_ENVELOPE_VARS = 4


@dataclass(frozen=True)
class EnvelopeTerm:
    """单个加性项的包络结果。"""

    src: str
    vars: tuple[str, ...]
    max_abs: float
    at: tuple[float, ...]

    @property
    def ok(self) -> bool:
        return self.max_abs <= DEFAULT_ENVELOPE_LIMIT


def split_additive_terms(node: ast.AST) -> list[ast.AST]:
    """把顶层 `a + b - c` 拆成加性项列表（`Sub` 的右操作数视为 +(-x)）。"""
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        return split_additive_terms(node.left) + split_additive_terms(node.right)
    return [node]


def _term_metrics(node: ast.AST) -> list[str]:
    names: list[str] = []

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.Name) and n.id in METRIC_INDEX and n.id not in names:
            names.append(n.id)
        for child in ast.iter_child_nodes(n):
            walk(child)

    walk(node)
    return names


def symbolic_envelope(
    parsed: ParsedFormula,
    params: Mapping[str, float],
    ranges: Mapping[str, tuple[float, float]] | None = None,
    limit: float = DEFAULT_ENVELOPE_LIMIT,
    allow_extended_funcs: bool = False,
) -> list[EnvelopeTerm]:
    """逐加性项角点求值（评审 P1-3 务实版）：放弃全局符号上界，只枚举每项引用的
    1–4 个指标变量的 2^n 角点组合。

    目的不是证明公式「正确」，而是**在启动期**拦住 `exp(kills)`、`kills**8`
    这类会让 Φ 爆到 1e30、把 GAE 与 value 头一起带走的公式。
    """
    rng: dict[str, tuple[float, float]] = dict(DEFAULT_RANGES)
    if ranges:
        for _k, _v in ranges.items():
            rng[_k] = (float(_v[0]), float(_v[1]))
    out: list[EnvelopeTerm] = []
    body = parsed.tree.body
    for term in split_additive_terms(body):
        names = _term_metrics(term)
        k = len(names)
        if k > MAX_ENVELOPE_VARS:
            # 引用变量太多 → 只取全下界/全上界两点粗筛（不枚举 2^n）
            corners = [
                tuple(rng[n][0] for n in names),
                tuple(rng[n][1] for n in names),
            ]
        else:
            corners = []
            for mask in range(1 << k):
                corners.append(
                    tuple(rng[n][1 if (mask >> b) & 1 else 0] for b, n in enumerate(names))
                )
        met = np.zeros((len(corners), len(METRICS)), dtype=np.float64)
        for j, corner in enumerate(corners):
            for name, val in zip(names, corner, strict=True):
                met[j, METRIC_INDEX[name]] = val
        try:
            sub = compile_formula(ast.unparse(term), params, allow_extended_funcs)
            vals = sub.phi(met, params)
        except (FormulaError, SyntaxError, ZeroDivisionError, FloatingPointError):
            # 角点上的除零/溢出本身就是要拦的信号 → 记为超限
            out.append(EnvelopeTerm(ast.unparse(term)[:80], tuple(names), float("inf"), ()))
            continue
        finite = vals[np.isfinite(vals)]
        peak = float(np.max(np.abs(finite))) if finite.size else float("inf")
        j = int(np.argmax(np.abs(vals))) if vals.size else 0
        out.append(
            EnvelopeTerm(
                src=ast.unparse(term)[:80],
                vars=tuple(names),
                max_abs=peak,
                at=corners[j] if corners else (),
            )
        )
    return out


@dataclass(frozen=True)
class ValidationReport:
    """静态校验 + 包络结果。"""

    spec: RewardSpec
    compiled: CompiledFormula | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    envelope: tuple[EnvelopeTerm, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_reward(
    spec: RewardSpec,
    ranges: Mapping[str, tuple[float, float]] | None = None,
    limit: float = DEFAULT_ENVELOPE_LIMIT,
) -> ValidationReport:
    """启动期校验：白名单/变量域（硬失败）+ 数值包络（warn，可 fail_on_violation）。"""
    errors: list[str] = []
    warnings: list[str] = []
    compiled: CompiledFormula | None = None
    envelope: tuple[EnvelopeTerm, ...] = ()

    if not spec.formula and not spec.builtin:
        errors.append("reward 既无 formula 也无 builtin")
    if spec.formula:
        try:
            compiled = compile_formula(spec.formula, spec.params, spec.allow_extended_funcs)
            envelope = tuple(
                symbolic_envelope(
                    compiled.parsed,
                    spec.params,
                    ranges,
                    limit,
                    allow_extended_funcs=spec.allow_extended_funcs,
                )
            )
            for t in envelope:
                if not t.ok:
                    warnings.append(
                        f"数值包络超限：|{t.src}| 峰值 {t.max_abs:.3g} > {limit:.3g}"
                        f"（vars={t.vars} @ {t.at}）"
                    )
        except FormulaDegradeError as e:
            # 评审 F1：仅量化触发面（长度/深度）可回退；其余 FormulaError 是配置错误 → 硬错
            if spec.builtin:
                warnings.append(f"formula 触发降级卡，回退内置 '{spec.builtin}'：{e}")
            else:
                errors.append(f"formula 超限且无 builtin：{e}")
        except FormulaError as e:
            errors.append(f"formula 非法（配置错误，不回退 builtin）：{e}")
    if spec.scheme not in ("toy", "score_reconcile"):
        errors.append(f"scheme 非法：'{spec.scheme}'（toy/score_reconcile）")
    for k in spec.terminal:
        if k not in ("stage_clear", "lives_exhausted", "timeout", "base_destroyed"):
            errors.append(f"terminal 键非法：'{k}'（须为 outcome 原名）")
    for k in spec.param_schedule:
        if k not in spec.params:
            warnings.append(f"param_schedule['{k}'] 不在 params 中（将由 schedule 单独提供）")

    return ValidationReport(
        spec=spec,
        compiled=compiled,
        errors=tuple(errors),
        warnings=tuple(warnings),
        envelope=envelope,
    )


__all__ = [
    "DEFAULT_ENVELOPE_LIMIT",
    "DEFAULT_RANGES",
    "EnvelopeTerm",
    "ValidationReport",
    "split_additive_terms",
    "symbolic_envelope",
    "validate_reward",
]

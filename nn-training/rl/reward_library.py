"""reward_library —— 奖励公式引擎（M1a，plan/rl-training-config.md §4.3）。

奖励的**唯一定义源在 Python**：TS 每决策步只落 21 维指标向量（`metrics.npy`，
`[N+1,21]` f8），奖励由课程配置里的 `formula` 定义并在此求值。**没有命名
scheme** —— 旧课程（kill/kill2/balanced/dodge-mix）与 v7 都是配置公式，它们是
公式引擎的验收用例，不是引擎之外的第二机制。

三段式（评审 R2-1）：
    parse   —— `ast.parse` + 白名单递归校验（一次性，配置加载期）
    compile —— 缓存 AST，每 iter 解析一次（含 param_schedule 折算）
    eval    —— 向量化：对 `[N+1,21]` 指标矩阵一次算完 Φ，禁逐 step Python 循环

Φ → reward 的 wrapper（§4.3.3，golden 锁定的对象）：

    Φ        = formula(metrics)                  # [N+1]：行 0..N-1 决策快照，行 N 终局快照
    r[i]     = Φ[i+1] − Φ[i]                    # i ∈ [0, N-1]
    r[N-1]  += reconcile(outcome, Φ[0], Φ[N], gatedScore)
        · toy              → terminal[outcome]
        · score_reconcile  → REWARD_SCALE·gatedScore − (Φ[N] − Φ[0])   # telescoping

安全边界（评审 R1-2 / LC §2.2）：无 `eval`/`exec`/`compile`；禁 `Attribute`/
`Subscript`/`Import`/`Lambda`/赋值/推导式 ⇒ 够不到文件系统、OS、import；白名单
全为纯 numpy ufunc（无 I/O、无副作用、确定性）；**白名单中不存在任何时间轴
（跨决策步）归约函数**（`sum`/`mean`/`cumsum`/`prod`/一元 `max` 等全不在）——
既杜绝「跨决策步混算」的口径错误，也防内存/数值爆炸。唯一例外 `wavg` 是
**特征轴**归约（同一行内定长 `(v,w)` 对），不跨样本。

公式限长 ≤1024 字符、AST 深度 ≤64（解析层 DoS 防线）；求值后 `isfinite` 检查
（除零/溢出 NaN 不得静默污染 GAE）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ---------------------------------------------------------------- 指标向量

#: 21 维指标名（TS 侧 `metrics.npy` 的列序，与 plan §4.1 逐项对齐）。
#: 变更此元组 = shard 格式变更，必须同步 `METRICS_VERSION` 与 TS 落盘端。
#:
# > **idx10 勘误（v8 表 vs 正文）**：plan §4.1 的表只列出 20 个名字（左列 0–9、
#: 右列 11–20），但正文两处断言「21 维连续编号」。取「连续 + 已编号项不变」的
#: 唯一解——把表里空缺的 **idx10 补 `starsCollected`**（TS `Telemetry` 里唯一
#: 尚未入表、且已现成计数的字段）：baseAlive=11 … enemyTotal=20 全部保持原位，
#: 索引连续无空槽。`startLives` 按 plan 由 `player.lives`/difficulty 派生，
#: 走 `params`，不占向量维。
METRICS: tuple[str, ...] = (
    "ticks",  # 0
    "kills",  # 1
    "lives",  # 2
    "playerHits",  # 3
    "playerDamageTaken",  # 4
    "playerShots",  # 5
    "enemyHits",  # 6
    "powerUpsCollected",  # 7
    "powerUpsSpawned",  # 8
    "stuckTicks",  # 9
    "starsCollected",  # 10  ← 补 plan §4.1 表的空槽（见上）
    "baseAlive",  # 11
    "baseWallTotal",  # 12
    "baseWallIntact",  # 13
    "basePressureSum",  # 14
    "basePressureSamples",  # 15
    "firstKillTick",  # 16  无首杀 = -1（TS undefined 的哨兵）
    "playerDeaths",  # 17
    "cellsVisited",  # 18
    "playerLevel",  # 19
    "enemyTotal",  # 20  静态（本局敌人总数）
)

METRIC_INDEX: dict[str, int] = {name: i for i, name in enumerate(METRICS)}
METRICS_DIM = len(METRICS)
#: shard manifest 版本：`[N+1,21]` 布局。任何用 `shape[0]` 推 episode 长度的
#: 下游在版本不匹配时必须响亮报错，而非静默错读（评审 LC §1.1）。
METRICS_VERSION = 2

#: 终局 outcome 名（与 TS `manifest.outcome` 同源）；未列出的 terminal 键 = 0。
OUTCOMES: tuple[str, ...] = ("stage_clear", "lives_exhausted", "timeout", "base_destroyed")

# ---------------------------------------------------------------- 白名单

MAX_FORMULA_CHARS = 1024
MAX_AST_DEPTH = 64

#: 函数名 → 白名单层。核心层默认开放；扩展层需 `allow_extended_funcs: true`。
CORE_FUNCS: tuple[str, ...] = (
    "abs",
    "min",
    "max",
    "clip",
    "where",
    "log",
    "exp",
    "pow",
    "sqrt",
    "wavg",
    "isfinite",
)
EXT_FUNCS: tuple[str, ...] = (
    "cbrt",
    "square",
    "expm1",
    "log2",
    "log10",
    "log1p",
    "sin",
    "cos",
    "tan",
    "asin",
    "acos",
    "atan",
    "atan2",
    "sinh",
    "cosh",
    "tanh",
    "floor",
    "ceil",
    "round",
    "trunc",
    "sign",
    "erf",
    "hypot",
)
#: 白名单常量（非常量 Name 之外的自由符号）。
CONST_NAMES: tuple[str, ...] = ("pi", "e")

#: 定长参数个数（None = 变长；`wavg` 单独校验偶数个）。
_ARITY: dict[str, tuple[int, int | None]] = {
    "abs": (1, 1),
    "min": (2, 2),  # np.minimum —— 二元逐元素，不是归约
    "max": (2, 2),  # np.maximum —— 同上
    "clip": (3, 3),
    "where": (3, 3),
    "log": (1, 1),
    "exp": (1, 1),
    "pow": (2, 2),
    "sqrt": (1, 1),
    "wavg": (2, None),
    "isfinite": (1, 1),
    "cbrt": (1, 1),
    "square": (1, 1),
    "expm1": (1, 1),
    "log2": (1, 1),
    "log10": (1, 1),
    "log1p": (1, 1),
    "sin": (1, 1),
    "cos": (1, 1),
    "tan": (1, 1),
    "asin": (1, 1),
    "acos": (1, 1),
    "atan": (1, 1),
    "atan2": (2, 2),
    "sinh": (1, 1),
    "cosh": (1, 1),
    "tanh": (1, 1),
    "floor": (1, 1),
    "ceil": (1, 1),
    "round": (1, 1),
    "trunc": (1, 1),
    "sign": (1, 1),
    "erf": (1, 1),
    "hypot": (2, 2),
}

#: 时间轴（跨决策步）归约函数名——**一个都不许出现在白名单里**。
#: 单测 `test_reward_golden.py::test_no_time_axis_reducers` 锁死不变式。
TIME_AXIS_REDUCERS: frozenset[str] = frozenset(
    {
        "sum",
        "mean",
        "median",
        "std",
        "var",
        "prod",
        "cumsum",
        "cumprod",
        "average",
        "any",
        "all",
        "argmin",
        "argmax",
        "amax",
        "amin",
        "nanmax",
        "nanmin",
        "ptp",
        "percentile",
        "quantile",
        "sort",
        "trace",
        "diff",
        "nansum",
        "nanmean",
        "count_nonzero",
        "reduce",
    }
)


def _wavg(*args: Any) -> np.ndarray:
    """条件加权平均（唯一允许的归约 helper）：Σ vᵢ·wᵢ / Σ wᵢ，null 维用 wᵢ=0 剔除。

    **特征轴**归约：只在同一样本行的定长 `(v,w)` 对之间归约，不跨决策步。
    `Σw == 0` 时返回 0（守卫除零，与 TS `wsum > 0 ? acc/wsum : 0` 同语义）。
    """
    if len(args) % 2 != 0:
        raise FormulaError(f"wavg 需要偶数个参数（(v,w) 对），收到 {len(args)} 个")
    num: np.ndarray | None = None
    den: np.ndarray | None = None
    for v, w in zip(args[0::2], args[1::2], strict=True):
        v = np.asarray(v, dtype=np.float64)
        w = np.asarray(w, dtype=np.float64)
        term = v * w
        num = term if num is None else num + term
        den = w if den is None else den + w
    if num is None or den is None:
        return np.zeros(())
    nz = den != 0
    return np.where(nz, num / np.where(nz, den, 1.0), 0.0)


def _funcs(allow_extended: bool) -> dict[str, Callable[..., Any]]:
    core: dict[str, Callable[..., Any]] = {
        "abs": np.abs,
        "min": np.minimum,
        "max": np.maximum,
        "clip": np.clip,
        "where": np.where,
        "log": np.log,
        "exp": np.exp,
        "pow": np.power,
        "sqrt": np.sqrt,
        "wavg": _wavg,
        "isfinite": np.isfinite,
    }
    if not allow_extended:
        return core
    core.update(
        {
            "cbrt": np.cbrt,
            "square": np.square,
            "expm1": np.expm1,
            "log2": np.log2,
            "log10": np.log10,
            "log1p": np.log1p,
            "sin": np.sin,
            "cos": np.cos,
            "tan": np.tan,
            "asin": np.arcsin,
            "acos": np.arccos,
            "atan": np.arctan,
            "atan2": np.arctan2,
            "sinh": np.sinh,
            "cosh": np.cosh,
            "tanh": np.tanh,
            "floor": np.floor,
            "ceil": np.ceil,
            "round": np.round,
            "trunc": np.trunc,
            "sign": np.sign,
            "erf": getattr(np, "erf", np.vectorize(math.erf)),
            "hypot": np.hypot,
        }
    )
    return core


def whitelist(allow_extended: bool = False) -> tuple[str, ...]:
    """当前生效的函数白名单（核心层，或核心+扩展层）。"""
    return CORE_FUNCS + EXT_FUNCS if allow_extended else CORE_FUNCS


class FormulaError(ValueError):
    """公式非法（解析/校验/求值任一阶段）。"""


class FormulaDegradeError(FormulaError):
    """公式**量化触发面**超限（评审 F1，2026-09-02）。

    降级卡只允许在**机制性量化限制**上触发：formula > 1024 字符 / AST 深度 > 64
    ——这两类与公式语义无关，配置方「换 builtin」是合法备胎。语法错误、白名单
    外符号、未知名 params 等都是**配置错误**（手误即脚枪），必须原样传播、
    由调用方响亮报错——绝不静默回退 builtin 掩盖真因。
    """


# ---------------------------------------------------------------- parse


_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.IfExp,
    ast.Load,
)

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.divide,
    ast.Pow: np.power,
    ast.FloorDiv: np.floor_divide,
    ast.Mod: np.remainder,
}
_UNARYOPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: lambda x: x,
    ast.USub: np.negative,
    ast.Not: np.logical_not,
}
_CMPOPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: np.equal,
    ast.NotEq: np.not_equal,
    ast.Lt: np.less,
    ast.LtE: np.less_equal,
    ast.Gt: np.greater,
    ast.GtE: np.greater_equal,
}


@dataclass(frozen=True)
class ParsedFormula:
    """校验后的公式：源码 + AST + 自由符号集合。"""

    source: str
    tree: ast.Expression
    metric_names: frozenset[str] = frozenset()
    param_names: frozenset[str] = frozenset()
    func_names: frozenset[str] = frozenset()
    depth: int = 0


def parse_formula(
    source: str, params: Mapping[str, float], allow_extended: bool = False
) -> ParsedFormula:
    """§4.3.2 第 1-2 步：parse + 白名单递归校验。非法即 `FormulaError`。"""
    if not isinstance(source, str) or not source.strip():
        raise FormulaError("formula 为空")
    if len(source) > MAX_FORMULA_CHARS:
        raise FormulaDegradeError(
            f"formula 长度 {len(source)} > {MAX_FORMULA_CHARS}（降级卡量化触发：请缩短公式"
            "或改用 reward.builtin 内置实现）"
        )
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"formula 语法错误: {e}") from None

    funcs = set(whitelist(allow_extended))
    metrics: set[str] = set()
    params_used: set[str] = set()
    funcs_used: set[str] = set()

    def check(node: ast.AST, depth: int, is_func: bool) -> int:
        if depth > MAX_AST_DEPTH:
            raise FormulaDegradeError(f"formula AST 深度 > {MAX_AST_DEPTH}（降级卡量化触发）")
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"禁止的 AST 节点：{type(node).__name__}（{ast.dump(node)[:80]}）")
        max_d = depth
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, bool)) or isinstance(node.value, complex):
                raise FormulaError("只允许数值/布尔常量（字符串常量禁止）")
        elif isinstance(node, ast.Name):
            name = node.id
            if is_func:
                if name not in funcs:
                    extra = (
                        "（扩展层函数，需 allow_extended_funcs: true）" if name in EXT_FUNCS else ""
                    )
                    raise FormulaError(f"白名单外的函数：{name}{extra}")
                funcs_used.add(name)
            elif name in METRIC_INDEX:
                metrics.add(name)
            elif name in params:
                params_used.add(name)
            elif name in CONST_NAMES:
                pass
            else:
                raise FormulaError(
                    f"未知符号 '{name}'：只允许指标变量 / params 键 / pi,e"
                    f"（当前 params 键 = {sorted(params)}）"
                )
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS:
                raise FormulaError(f"禁止的运算符：{type(node.op).__name__}")
            max_d = max(
                max_d, check(node.left, depth + 1, False), check(node.right, depth + 1, False)
            )
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARYOPS:
                raise FormulaError(f"禁止的一元运算符：{type(node.op).__name__}")
            max_d = check(node.operand, depth + 1, False)
        elif isinstance(node, ast.BoolOp):
            if not isinstance(node.op, (ast.And, ast.Or)):
                raise FormulaError(f"禁止的布尔运算符：{type(node.op).__name__}")
            for v in node.values:
                max_d = max(max_d, check(v, depth + 1, False))
        elif isinstance(node, ast.Compare):
            if len(node.ops) != 1 or type(node.ops[0]) not in _CMPOPS:
                raise FormulaError("只允许单段比较（== != < <= > >=）")
            max_d = max(
                max_d,
                check(node.left, depth + 1, False),
                check(node.comparators[0], depth + 1, False),
            )
        elif isinstance(node, ast.Call):
            if node.keywords:
                raise FormulaError("函数调用禁止关键字参数")
            lo, hi = _ARITY.get(getattr(node.func, "id", ""), (0, 0))
            n = len(node.args)
            if n < lo or (hi is not None and n > hi) or (hi is None and n % 2 != 0):
                raise FormulaError(
                    f"{getattr(node.func, 'id', '?')} 参数个数非法（收到 {n}，"
                    f"要求{'偶数个 (v,w) 对' if hi is None else f'{lo}'}）"
                )
            max_d = check(node.func, depth + 1, True)
            for a in node.args:
                max_d = max(max_d, check(a, depth + 1, False))
        elif isinstance(node, ast.Expression):
            max_d = check(node.body, depth + 1, False)
        elif isinstance(node, ast.IfExp):
            max_d = max(
                max_d,
                check(node.test, depth + 1, False),
                check(node.body, depth + 1, False),
                check(node.orelse, depth + 1, False),
            )
        return max_d

    depth = check(tree, 0, False)
    return ParsedFormula(
        source=source.strip(),
        tree=tree,
        metric_names=frozenset(metrics),
        param_names=frozenset(params_used),
        func_names=frozenset(funcs_used),
        depth=depth,
    )


# ---------------------------------------------------------------- compile / eval


class CompiledFormula:
    """向量化求值器：对 `[K,21]` 指标矩阵一次算出 `[K]` 的 Φ。

    `eval` 全程 numpy ufunc —— 无 Python 逐样本循环（评审 P1-2：9 万步/iter 下
    纯 Python ast-walk 会慢 10–100×）。
    """

    def __init__(self, parsed: ParsedFormula, allow_extended: bool = False) -> None:
        self._parsed = parsed
        self._funcs = _funcs(allow_extended)
        self._consts: dict[str, float] = {"pi": math.pi, "e": math.e}
        self._metric_cols: tuple[int, ...] = tuple(
            sorted(METRIC_INDEX[n] for n in parsed.metric_names)
        )

    @property
    def parsed(self) -> ParsedFormula:
        """底层 `ParsedFormula`（`reward_validation.symbolic_envelope` 需要 AST）。"""
        return self._parsed

    @property
    def source(self) -> str:
        return self._parsed.source

    @property
    def depth(self) -> int:
        return self._parsed.depth

    @property
    def metric_names(self) -> frozenset[str]:
        return self._parsed.metric_names

    @property
    def param_names(self) -> frozenset[str]:
        return self._parsed.param_names

    def describe(self) -> str:
        """AST 可读 dump（`--echo-config` 用；非哈希，人眼可核对）。"""
        return ast.dump(self._parsed.tree, annotate_fields=True, indent=1)

    def phi(self, metrics: np.ndarray, params: Mapping[str, float]) -> np.ndarray:
        """`metrics [K,21]` → `Φ [K]`（float64）。"""
        m = np.asarray(metrics, dtype=np.float64)
        if m.ndim != 2 or m.shape[1] != METRICS_DIM:
            raise FormulaError(
                f"metrics 形状应为 [K,{METRICS_DIM}]，收到 {m.shape}"
                f"（metrics_version={METRICS_VERSION}）"
            )
        env: dict[str, Any] = dict(self._consts)
        for name in self._parsed.metric_names:
            env[name] = m[:, METRIC_INDEX[name]]
        for name in self._parsed.param_names:
            if name not in params:
                raise FormulaError(f"公式引用的 params 键 '{name}' 缺失")
            env[name] = float(params[name])
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            out = self._eval(self._parsed.tree.body, env)
        out = np.asarray(out, dtype=np.float64)
        if out.shape != (m.shape[0],):
            out = np.broadcast_to(out, (m.shape[0],)).astype(np.float64)
        if not np.all(np.isfinite(out)):
            raise FormulaError("Φ 求值出现非有限值（除零/溢出）——拒绝污染 GAE")
        return out

    # ---- 递归求值 ----

    def _eval(self, node: ast.AST, env: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in METRIC_INDEX:  # 未使用的指标列（理论上到不了）
                raise FormulaError(f"指标 '{node.id}' 未绑定")
            raise FormulaError(f"未知符号 '{node.id}'")
        if isinstance(node, ast.BinOp):
            return _BINOPS[type(node.op)](self._eval(node.left, env), self._eval(node.right, env))
        if isinstance(node, ast.UnaryOp):
            return _UNARYOPS[type(node.op)](self._eval(node.operand, env))
        if isinstance(node, ast.BoolOp):
            vals = [self._eval(v, env) for v in node.values]
            op = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            out = vals[0]
            for v in vals[1:]:
                out = op(out, v)
            return out
        if isinstance(node, ast.Compare):
            return _CMPOPS[type(node.ops[0])](
                self._eval(node.left, env), self._eval(node.comparators[0], env)
            )
        if isinstance(node, ast.IfExp):
            return np.where(
                self._eval(node.test, env), self._eval(node.body, env), self._eval(node.orelse, env)
            )
        if isinstance(node, ast.Call):
            fname = node.func.id if isinstance(node.func, ast.Name) else ""
            fn = self._funcs[fname]
            return fn(*[self._eval(a, env) for a in node.args])
        raise FormulaError(f"无法求值的节点：{type(node).__name__}")


def compile_formula(
    source: str, params: Mapping[str, float], allow_extended: bool = False
) -> CompiledFormula:
    """parse + compile 一步到位（每 iter 解析一次，勿放进逐 shard 循环）。"""
    return CompiledFormula(parse_formula(source, params, allow_extended), allow_extended)


# ---------------------------------------------------------------- param_schedule


@dataclass(frozen=True)
class ScheduleSpec:
    """`param_schedule` 单键规格：`{from, to, until_iter, mode}`。

    - `linear`：`it=1` 取 `from`，`it=until_iter` 取 `to`，线性插值；窗口结束停在 `to`。
    - `step`：`it < until_iter` 取 `from`，否则取 `to`。
    - `mode` 不做 enum 限制（前向兼容 cosine/exponential），未知 mode 直接报错（LC §4.2）。
    """

    fro: float
    to: float
    until_iter: int
    mode: str = "linear"

    @classmethod
    def from_dict(cls, key: str, d: Mapping[str, Any]) -> ScheduleSpec:
        missing = {"from", "to", "until_iter"} - set(d)
        if missing:
            raise FormulaError(f"param_schedule['{key}'] 缺字段 {sorted(missing)}")
        mode = str(d.get("mode", "linear"))
        if mode not in ("linear", "step"):
            raise FormulaError(
                f"param_schedule['{key}'].mode='{mode}' 未知（linear/step；"
                "新 mode 需先在本模块实现）"
            )
        until = int(d["until_iter"])
        if until < 1:
            raise FormulaError(f"param_schedule['{key}'].until_iter 非法（≥1）")
        return cls(fro=float(d["from"]), to=float(d["to"]), until_iter=until, mode=mode)

    def value_at(self, it: int) -> float:
        if self.mode == "step":
            return self.to if it >= self.until_iter else self.fro
        # linear：it=1 → from；it=until_iter → to
        if self.until_iter <= 1:
            return self.to
        t = (int(it) - 1) / (self.until_iter - 1)
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        return self.fro + (self.to - self.fro) * t


# ---------------------------------------------------------------- reward fn


@dataclass(frozen=True)
class RewardSpec:
    """课程配置的 `reward` 块（不可变：holder 每 iter 持有一份，禁 in-place）。"""

    formula: str = ""
    params: Mapping[str, float] = field(default_factory=dict)
    param_schedule: Mapping[str, ScheduleSpec] = field(default_factory=dict)
    terminal: Mapping[str, float] = field(default_factory=dict)
    scheme: str = "toy"  # "toy" | "score_reconcile"
    reward_scale: float = 10.0
    allow_extended_funcs: bool = False
    builtin: str = ""  # 降级卡回退目标（空 = 超限即硬失败）

    def identity(self) -> str:
        """reward 血缘指纹（review 勘误 2：命名 scheme 已撤销，只记 formula 指纹）。"""
        blob = json.dumps(
            {
                "formula": self.formula or f"<builtin:{self.builtin}>",
                "params": dict(sorted(self.params.items())),
                "schedule": {
                    k: [v.fro, v.to, v.until_iter, v.mode]
                    for k, v in sorted(self.param_schedule.items())
                },
                "terminal": dict(sorted(self.terminal.items())),
                "scheme": self.scheme,
                "scale": self.reward_scale,
                "ext": self.allow_extended_funcs,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class RewardFn:
    """配置公式 → 每局奖励序列。

    `scheme='toy'`：末样本 `+= terminal[outcome]`（未列出的 outcome = 0）。
    `scheme='score_reconcile'`：末样本 `+= scale·gatedScore − (Φ[N]−Φ[0])`，
    使 Σr ≡ scale·gatedScore（telescoping 恒等式）。
    """

    def __init__(self, spec: RewardSpec) -> None:
        self.spec = spec
        self._compiled: CompiledFormula | None = None
        self._builtin: Callable[[np.ndarray, Mapping[str, float]], np.ndarray] | None = None
        if spec.formula:
            try:
                self._compiled = compile_formula(
                    spec.formula, spec.params, spec.allow_extended_funcs
                )
            except FormulaDegradeError as e:
                if not spec.builtin:
                    raise
                # 降级卡：**仅量化触发面**（长度/深度超限）回退内置，warning 带真实原因；
                # 语法/白名单/未知名等 FormulaError 不在此捕获——原样传播响亮报错
                # （评审 F1：调参手误不得静默回退掩盖真因）。
                from rl.log import log

                log(
                    f"[reward] WARNING: formula 触发降级卡（{e}）—— 回退内置 "
                    f"'{spec.builtin}'（第二机制，DoD 已承认；formula={spec.formula[:60]}…）"
                )
        if self._compiled is None:
            if not spec.builtin:
                raise FormulaError("reward 既无 formula 也无 builtin")
            from rl.reward_builtin import get_builtin

            self._builtin = get_builtin(spec.builtin)

    # ---- 参数折算 ----

    def resolve_params(self, it: int) -> dict[str, float]:
        """`params` 叠加该 iter 的 `param_schedule` 折算（窗口内 schedule 优先）。"""
        out = {k: float(v) for k, v in self.spec.params.items()}
        for key, sch in self.spec.param_schedule.items():
            out[key] = sch.value_at(it)
        return out

    def phi(self, metrics: np.ndarray, it: int) -> np.ndarray:
        params = self.resolve_params(it)
        if self._compiled is not None:
            return self._compiled.phi(metrics, params)
        assert self._builtin is not None
        return self._builtin(metrics, params)

    # ---- wrapper（§4.3.3）----

    def __call__(
        self,
        metrics: np.ndarray,
        outcome: str,
        gated_score: float = 0.0,
        it: int = 1,
    ) -> np.ndarray:
        """`metrics [N+1,21]` + outcome → `reward [N]`（float64）。"""
        m = np.asarray(metrics, dtype=np.float64)
        if m.ndim != 2 or m.shape[0] < 2:
            raise FormulaError(f"metrics 至少 2 行（N 个决策快照 + 1 个终局快照），收到 {m.shape}")
        phi = self.phi(m, it)
        r = np.diff(phi)  # r[i] = Φ[i+1] − Φ[i]，共 N 个样本
        if self.spec.scheme == "score_reconcile":
            r[-1] += self.spec.reward_scale * float(gated_score) - (phi[-1] - phi[0])
        else:
            r[-1] += float(self.spec.terminal.get(outcome, 0.0))
        if not np.all(np.isfinite(r)):
            raise FormulaError(f"reward 出现非有限值（outcome={outcome}）——拒绝污染 GAE")
        return r


def build_reward_fn(spec: RewardSpec) -> RewardFn:
    return RewardFn(spec)


def reward_from_spec(
    spec: RewardSpec,
    metrics: np.ndarray,
    outcome: str,
    gated_score: float = 0.0,
    it: int = 1,
) -> np.ndarray:
    """一次性便捷入口（测试/诊断用；训练循环走 RewardFn 复用编译结果）。"""
    return build_reward_fn(spec)(metrics, outcome, gated_score, it)


# ---------------------------------------------------------------- 自检


def assert_no_time_axis_reducers() -> None:
    """白名单不变式自检：不得出现任何时间轴归约函数，且 min/max 必须是二元的。"""
    overlap = (set(CORE_FUNCS) | set(EXT_FUNCS)) & TIME_AXIS_REDUCERS
    assert not overlap, f"白名单混入时间轴归约函数：{sorted(overlap)}"


def _self_check() -> None:
    assert_no_time_axis_reducers()
    assert len(METRICS) == METRICS_DIM == 21, METRICS_DIM
    assert len(set(METRICS)) == METRICS_DIM


_self_check()


__all__ = [
    "CORE_FUNCS",
    "EXT_FUNCS",
    "MAX_AST_DEPTH",
    "MAX_FORMULA_CHARS",
    "METRICS",
    "METRICS_DIM",
    "METRICS_VERSION",
    "METRIC_INDEX",
    "OUTCOMES",
    "TIME_AXIS_REDUCERS",
    "CompiledFormula",
    "FormulaError",
    "ParsedFormula",
    "RewardFn",
    "RewardSpec",
    "ScheduleSpec",
    "build_reward_fn",
    "compile_formula",
    "parse_formula",
    "reward_from_spec",
    "whitelist",
]

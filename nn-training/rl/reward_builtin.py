"""reward_builtin —— 内置势函数（降级卡回退目标 + 公式编码的对账基准）。

plan §4.4 / LC §4.3 的「降级卡」要求：formula 超 `MAX_FORMULA_CHARS`、AST 深
`MAX_AST_DEPTH` 或含白名单外符号时回退内置实现（warning 日志，不静默切换）。
本模块承载内置实现，同时充当**公式编码的对账基准**——同一份 counter 向量下
`v7_phi` 与 v7 配置公式必须逐位一致（tolerance ≤1e-9），否则说明公式写错了。

**这是第二机制，不是默认路径**：正常课程走 `reward.formula`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np

from rl.reward_library import METRIC_INDEX

#: 内置实现注册表（名字 → 可调用）。添加新内置即在此登记。
_REGISTRY: dict[str, Callable[[np.ndarray, Mapping[str, float]], np.ndarray]] = {}


def _register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn

    return deco


def get_builtin(name: str) -> Callable[[np.ndarray, Mapping[str, float]], np.ndarray]:
    if name not in _REGISTRY:
        raise KeyError(f"未知的 reward.builtin：'{name}'（已注册：{sorted(_REGISTRY)}）")
    return _REGISTRY[name]


def builtin_names() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# ---------------------------------------------------------------- v7


def _clamp01(x: np.ndarray) -> np.ndarray:
    """TS `clamp01`：非有限 → 0（NaN 守卫与 TS 一致）。"""
    return np.where(np.isfinite(x), np.clip(x, 0.0, 1.0), 0.0)


def _ramp(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """TS `ramp(x, lo, hi)`：`hi == lo` 时取 `x >= hi ? 1 : 0`。"""
    if hi == lo:
        return np.where(x >= hi, 1.0, 0.0)
    return _clamp01((x - lo) / (hi - lo))


@_register("v7")
def v7_phi(metrics: np.ndarray, params: Mapping[str, float]) -> np.ndarray:
    """v7 势 Φ —— `tools/sim/rl-reward.ts::phiNow` 的向量化忠实移植。

        Φ = S · B · lossPartialQ(counters) · (baseAlive ? 1 : M)

    params 键：**与 `curricula/s4b.jsonc` 的 `reward.params` 逐名一致**——内置实现
    是公式的降级回退目标，两者必须可在同一份 params 下互换。`S`（REWARD_SCALE=10）、
    `B`（V7 lossBandMax=0.4）、`M`（BASE_LOSS_MULT=0.1）、`kpmRef`（8）、
    `accRef`（0.3）、`startLives`、以及 8 个维度权重 `wP`（progress）、`wL`（lives）、
    `wBI`（baseIntegrity）、`wT`（tempo）、`wA`（accuracy）、`wLo`（loot）、
    `wBS`（baseSafety）、`wO`（openingTempo）。权重 ≤0 的维度整体剔除，与 TS
    `add()` 同规则。

    null 维（值不可算）**不进分子也不进分母**——TS `add(key, null)` 直接 return。
    """
    m = np.asarray(metrics, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError(f"metrics 应为 [K,21]，收到 {m.shape}")

    def col(name: str) -> np.ndarray:
        return m[:, METRIC_INDEX[name]]

    ticks = col("ticks")
    kills = col("kills")
    lives = col("lives")
    playerShots = col("playerShots")
    powerUpsCollected = col("powerUpsCollected")
    powerUpsSpawned = col("powerUpsSpawned")
    baseAlive = col("baseAlive")
    baseWallTotal = col("baseWallTotal")
    baseWallIntact = col("baseWallIntact")
    basePressureSum = col("basePressureSum")
    basePressureSamples = col("basePressureSamples")
    firstKillTick = col("firstKillTick")
    enemyTotal = col("enemyTotal")

    kpm_ref = float(params.get("kpmRef", 8.0))
    acc_ref = float(params.get("accRef", 0.3))
    start_lives = float(params.get("startLives", 2.0))
    minutes = ticks / 3600.0

    acc = np.zeros(m.shape[0], dtype=np.float64)
    wsum = np.zeros(m.shape[0], dtype=np.float64)

    def add(weight_key: str, v: np.ndarray, valid: np.ndarray) -> None:
        nonlocal acc, wsum
        w = float(params.get(weight_key, 0.0))
        if w <= 0.0:  # TS: weight undefined 或 <= 0 → 整维剔除
            return
        ww = np.where(valid, w, 0.0)
        acc = acc + ww * v
        wsum = wsum + ww

    # progress
    add(
        "wP",
        _clamp01(kills / np.where(enemyTotal > 0, enemyTotal, 1.0)),
        enemyTotal > 0,
    )
    # lives
    add(
        "wL",
        _clamp01(lives / np.where(start_lives > 0, start_lives, 1.0)),
        np.full(m.shape[0], start_lives > 0),
    )
    # baseIntegrity：基地没了 → v=0 但**仍计权重**（TS 的 `!baseAlive ? 0 : ...` 分支）
    bi_valid = np.where(baseAlive > 0, baseWallTotal > 0, True)
    bi_v = np.where(
        baseAlive > 0,
        np.where(
            baseWallTotal > 0,
            0.55
            + 0.45 * _clamp01(baseWallIntact / np.where(baseWallTotal > 0, baseWallTotal, 1.0)),
            0.0,
        ),
        0.0,
    )
    add("wBI", bi_v, bi_valid)
    # tempo
    add(
        "wT",
        np.where(
            minutes > 0,
            _clamp01(kills / np.where(minutes > 0, minutes, 1.0) / kpm_ref) if kpm_ref > 0 else 0.0,
            0.0,
        ),
        np.full(m.shape[0], kpm_ref > 0),
    )
    # accuracy
    acc_valid = (playerShots > 0) & (acc_ref > 0)
    add(
        "wA",
        _clamp01(kills / np.where(playerShots > 0, playerShots, 1.0) / (acc_ref or 1.0)),
        acc_valid,
    )
    # loot
    add(
        "wLo",
        _clamp01(powerUpsCollected / np.where(powerUpsSpawned > 0, powerUpsSpawned, 1.0)),
        powerUpsSpawned > 0,
    )
    # baseSafety = 1 − mean(basePressure)
    add(
        "wBS",
        _clamp01(
            1.0 - basePressureSum / np.where(basePressureSamples > 0, basePressureSamples, 1.0)
        ),
        basePressureSamples > 0,
    )
    # openingTempo：无首杀 = 0，但**权重照计**（评审 P0-7）
    add(
        "wO",
        np.where(firstKillTick == -1, 0.0, 1.0 - _ramp(firstKillTick, 0.0, 1800.0)),
        np.ones(m.shape[0], dtype=bool),
    )

    q = np.where(wsum > 0, acc / np.where(wsum > 0, wsum, 1.0), 0.0)
    s = float(params.get("S", 10.0))
    b = float(params.get("B", 0.4))
    mm = float(params.get("M", 0.1))
    return np.asarray(s * b * q * np.where(baseAlive > 0, 1.0, mm), dtype=np.float64)


#: v7 默认权重（镜像 `tools/sim/rl-reward.ts::RL_LOSS_WEIGHTS` + 参考常量）。
V7_DEFAULT_PARAMS: dict[str, float] = {
    "S": 10.0,
    "B": 0.4,
    "M": 0.1,
    "kpmRef": 8.0,
    "accuracyRef": 0.3,
    "startLives": 2.0,
    "wP": 0.3,
    "wL": 0.0,
    "wBI": 0.25,
    "wT": 0.08,
    "wA": 0.06,
    "wLo": 0.03,
    "wBS": 0.25,
    "wO": 0.03,
}

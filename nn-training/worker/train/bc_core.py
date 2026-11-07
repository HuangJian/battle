"""train/bc_core.py — BC 训练器里**与 torch 无关的判据**（顶层零 torch，2026-09-26）。

## 为什么单独一个模块

`train/bc.py` 顶层 `import torch`（整个训练器都要），于是同住一个文件的**纯函数**
（「课程 JSONC / 本机 CLI / 云端 manifest 三种来路的旋钮怎么归一」）在测试里也只能连坐
torch——`tests/worker/test_bc_course.py::test_resolve_fire_pos_weight` 因此在没有 torch 的机器上必红。
按 `ppo/np_core.py` / `train/device.py` 的同一手法抽出来；`train/bc.py` 再导出，
既有 `from train.bc import resolve_fire_pos_weight` 一行不改。

判据：**输入是标量/字典、输出是标量/异常**，不碰张量、不碰 DataLoader ⇒ 放这里。
带 torch 的（`_masked_ce` / `_bc_raw` / 训练循环）留在 `train/bc.py`。
"""

from __future__ import annotations

import math

#: 平滑窗口（B3/B1，2026-11-05/07）：单点 val_loss 的抖动由 fire 头噪声主导（与
#: fire_acc 相关 −0.965），拿单点做「新低」等于在噪声里抽签。早停与选权重共用。
SMOOTH_WINDOW = 5
#: 平滑值改善小于该幅度不算「改善」（否则平盘会被当成缓慢进步而永不早停）。
EARLY_STOP_MIN_DELTA = 1e-4


def smoothed_val_loss(val_losses: list[float], window: int = SMOOTH_WINDOW) -> float:
    """最近 `window` 个 val_loss 的算术平均（不足 window 时用已有的全部）。"""
    recent = val_losses[-max(1, window) :]
    return sum(recent) / len(recent)


def early_stop_epoch(
    val_losses: list[float],
    patience: int,
    window: int = SMOOTH_WINDOW,
    min_delta: float = EARLY_STOP_MIN_DELTA,
) -> int | None:
    """该在第几个 epoch 停（1-based）？`None` = 不早停。

    判据：**滑动平均 val_loss** 连续 `patience` 个 epoch 没有改善（改善 = 比历史最佳
    平滑值低过 `min_delta`）。纯函数 ⇒ 早停语义可单测，不用真训练收敛曲线。
    """
    if patience <= 0:
        return None
    best = float("inf")
    best_at = 0
    for i in range(1, len(val_losses) + 1):
        sm = smoothed_val_loss(val_losses[:i], window)
        if sm < best - min_delta:
            best = sm
            best_at = i
            continue
        if best_at and i - best_at >= patience:
            return i
    return None


def select_best_epoch(val_losses: list[float], window: int = SMOOTH_WINDOW) -> int | None:
    """按**滑动平均** val_loss 选交付权重来自哪一 epoch（1-based；None = 窗口未满）。

    B1（2026-11-07）：单点 `min(val_loss)` 由 fire 头噪声主导，等于在噪声里抽签；
    只看**完整窗口**的平滑值 ⇒ 一次尖峰不可能把交付权重拉过来。返回窗口**末端**
    epoch（平滑曲线天然滞后：状态取窗口最后一天的权重）。训练不足 `window` 轮时返回
    None，调用方按「交最后一轮」处理，而不是退回单点最小。
    """
    w = max(1, window)
    if len(val_losses) < w:
        return None
    best_at = 0
    best = float("inf")
    for i in range(w, len(val_losses) + 1):
        sm = sum(val_losses[i - w : i]) / w
        if sm < best:
            best = sm
            best_at = i
    return best_at


def sanitize_json(o):
    """递归把 float NaN → None（json.dump 默认写出非标准 `NaN` 字面量，
    TS JSON.parse 会直接抛错——weights 文件里不能带 NaN）。"""
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, dict):
        return {k: sanitize_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sanitize_json(v) for v in o]
    return o


def resolve_fire_pos_weight(raw: object, counts: dict) -> float:
    """fire 头正例权重（2026-09-14）：`"auto"` = 训练集 neg/pos（≈12.6）；数 = 直接用；
    0/None = 关闭。为什么需要它：语料 fire 正例仅 ~7%，不加权的 CE 下 fire 头实测
    accuracy 0.770 < "永不发射"常数基线 0.927 —— 即该头是负增益。

    `raw` 来自三处：课程 JSONC（pydantic 已收窄为 float|"auto"）、本机 CLI（字符串）、
    云端 manifest（float|"auto"）—— 故字符串分支同时接受 "auto" 与数字字面量。

    非法输入（未知字符串 / 非数值类型）**响亮 raise**：旋钮写错时应当停在加载期，
    而不是静默退化成 0（= 关掉加权，训练照跑、指标悄悄变差）。
    """
    c_f = counts.get("fire") or {}
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == "auto":
            pos = int(c_f.get(1, 0))
            neg = int(c_f.get(0, 0))
            return (neg / pos) if pos > 0 else 0.0
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"fire_pos_weight 只接受数字或 'auto'，收到 {raw!r}") from None
    if raw is None:
        return 0.0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"fire_pos_weight 非法: {raw!r}")
    return float(raw)

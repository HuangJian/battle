"""止损判门（P1-9 统计化，2026-09-02 从 run_rl.py 拆出）。
"""

from __future__ import annotations


def eval_sigma(eval_rec: dict | None) -> float | None:
    """单次干净评估的 winRate 标准误 σ = √(p(1−p)/n)（二项分布）。

    返回 None = eval_rec 无 games/winRate 字段（旧调用方），调用侧回退旧语义。
    注意这是**乐观下界**：同 stage 的 n 局共享关卡、存在 stage 级相关性，真实 σ
    更大——因此"显著"判定是保守的（宁可少停，不可乱停）。
    """
    if not eval_rec:
        return None
    games = eval_rec.get("games")
    wr = eval_rec.get("winRate")
    if not isinstance(games, (int, float)) or games <= 0 or not isinstance(wr, (int, float)):
        return None
    p = max(0.0, min(1.0, float(wr)))
    return float((p * (1.0 - p) / games) ** 0.5)


def stop_loss_hit(
    mode: str,
    stop_loss_at: int,
    stop_loss_delta: float,
    it: int,
    eval_rec: dict | None,
    *,
    z_score: float = 2.0,
) -> bool:
    """预注册止损判门（D4 泛化：原 run_rl_intent 的 iter15 Δ≤0 硬编码）。纯函数。

    仅 intent/goal 模式生效；stop_loss_at=0 = 关闭（per-tick 恒 False）。

    **统计化修正（plan/python-refactor.md P1-9，2026-09-02）**：旧逻辑 `Δ ≤ delta`
    （默认 0.0）在评估 σ≈0.024（350 局 p≈0.72；stage 级相关下实为 0.03~0.04）时，
    真实中立策略单次评估约 50% 概率误停——抛硬币。新逻辑要求 Δ **显著**为负：
      trip = (Δ ≤ stop_loss_delta) ∧ (σ 未知 ∨ Δ ≤ −z·σ)
    eval_rec 带 games/winRate 时按二项分布推 σ（z=2 → 约 97.5% 置信单侧）；
    不带时（旧调用方）回退原语义。连击护栏（连续 ≥2 轮显著）在调用侧实现。
    """
    if mode == "per-tick" or stop_loss_at <= 0:
        return False
    if not (it >= stop_loss_at and eval_rec and eval_rec.get("delta") is not None):
        return False
    delta = float(eval_rec["delta"])
    if delta > stop_loss_delta:
        return False
    sigma = eval_sigma(eval_rec)
    if sigma is None:
        return True  # 无 games 数据：保持原语义（Δ≤bar 即停）
    return delta <= -z_score * sigma


"""F4 熔断 —— 跨迭代行为崩塌的纯逻辑判定（阈值与连击规则单点维护）。

历史背景（2026-08-22）：warnings had no teeth — the R3 long run kept going ~60
iterations past behavioral collapse because KL>0.15 never persisted two consecutive
iterations before it100 (spikes at it65/73/79 were singles). Two trip rules:

  KL:  kl >= KL_BREAK 连续 KL_BREAK_CONSEC 轮（剧烈漂移）
  ENT: entropy <= ENT_BREAK 连续 ENT_BREAK_CONSEC 轮 且 winRate < MAX_WINRATE
       （退化确定性；R3 崩塌在 0.42-0.55 停了 ~60 轮。winRate 护栏避免误停
       已收敛的高胜率策略。）

对 R3 的校验：ENT 规则 ~it70 触发（it63-it73 连续 11 轮 <0.60）；仅 KL 规则要到
it100 才触发——KL 是滞后指标，entropy 才是先导。
"""
from __future__ import annotations

KL_WARN = 0.08        # calibrated to our setup: healthy steady state is 0.045-0.054
ENT_COLLAPSE_DROP = 0.10  # single-iteration entropy drop that warrants a warning

KL_BREAK = 0.15
KL_BREAK_CONSEC = 3
ENT_BREAK = 0.60
ENT_BREAK_CONSEC = 8
ENT_BREAK_MAX_WINRATE = 0.5
CIRCUIT_EXIT_CODE = 3


def breaker_update(kl_streak: int, ent_streak: int, *, kl: float, entropy: float,
                   win_rate: float) -> tuple[int, int, str | None]:
    """推进连击计数并判定是否熔断。

    返回 (kl_streak, ent_streak, tripped)：tripped 为 None 表示继续训练，
    否则为可读的熔断原因（调用方写 circuit_break 事件并停车）。
    agg 缺失（流式 checkpoint-complete 轮无任何梯度步）由调用方短路，不计连击。
    """
    kl_streak = kl_streak + 1 if kl >= KL_BREAK else 0
    ent_streak = (ent_streak + 1
                  if entropy <= ENT_BREAK and win_rate < ENT_BREAK_MAX_WINRATE else 0)
    if kl_streak >= KL_BREAK_CONSEC:
        return kl_streak, ent_streak, \
            f"kl>={KL_BREAK} for {kl_streak} consecutive iters (now {kl:.3f})"
    if ent_streak >= ENT_BREAK_CONSEC:
        return kl_streak, ent_streak, (
            f"entropy<={ENT_BREAK} for {ent_streak} consecutive iters "
            f"(now {entropy:.3f}, winRate={win_rate})")
    return kl_streak, ent_streak, None

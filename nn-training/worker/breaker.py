"""F4 熔断 —— 跨迭代行为崩塌的纯逻辑判定（阈值与连击规则单点维护）。

历史背景（2026-08-22）：warnings had no teeth — the R3 long run kept going ~60
iterations past behavioral collapse because KL>0.15 never persisted two consecutive
iterations before it100 (spikes at it65/73/79 were singles). Two trip rules:

  KL:  kl >= KL_BREAK 连续 KL_BREAK_CONSEC 轮（剧烈漂移）
  ENT: entropy <= ENT_BREAK 连续 ENT_BREAK_CONSEC 轮 且 winRate < MAX_WINRATE
       （退化确定性；R3 崩塌在 0.42-0.55 停了 ~60 轮。winRate 护栏避免误停
       已收敛的高胜率策略。）

2026-09-06 修正（p4-onset it8 误熔断，DECISIONS §339）：ENT 规则改为**相对崩塌**
语义，且三个阈值课程可配。

误熔断现场：p4-onset 从 p1-ep60 BC 权重热启动（蒸馏策略天然尖锐），it1 熵 0.254、
之后一路**升到** 0.36-0.38，winRate 5-9%。旧规则「entropy<=0.60 连续 8 轮 且
winRate<0.5」把「天生低熵」当成「崩塌」→ it8 满 8 连击误停；winRate 护栏本意是
"别停已收敛的高胜率策略"，在"起点就低于 0.5、目标就是往上爬"的热启动课程里反而
成了必停条件。

新语义（ent_peak = 本轮之前见过的最大熵）：
  - ent_peak 为 None（冷启动首轮 / 调用方不追踪峰值）→ 退回旧的绝对电平判定
    （保持历史行为与旧调用点兼容）
  - 有峰值时，须满足**相对崩塌**之一才计连击：
      ① 峰值曾高于阈值、现在跌到阈值以下（ent_peak > ent_break 且 entropy <= ent_break）
      ② 相对峰值跌幅 >= ENT_COLLAPSE_DROP（低熵课程里仍在持续下跌也算崩塌）
  效果：热启动课程（峰值与当前都在 0.36 带、无跌幅）ENT 规则自动失效；冷启动课程
  （R3：峰值 ~1.0 跌到 0.42-0.55，跌幅 ≫ 0.10）行为与旧规则一致。

对 R3 的校验：ENT 规则 ~it70 触发（it63-it73 连续 11 轮 <0.60）；仅 KL 规则要到
it100 才触发——KL 是滞后指标，entropy 才是先导。
"""

from __future__ import annotations

KL_WARN = 0.04  # 2026-09-02 P0-3 口径换算 ×0.5（Schulman 无偏估计量，旧 2× 口径 0.08）；
# 健康稳态即"0.045-0.054"为旧口径历史值 → 新口径 0.0225-0.027，见下文换算表。
ENT_COLLAPSE_DROP = 0.10  # single-iteration entropy drop that warrants a warning

# P0-3 阈值换算表（2026-09-02）：估计量 (Δlnπ)²·0.5 → Schulman (r-1)-ln r 后，全部
# KL 阈值 ÷2 保持行为等价：KL_WARN 0.08→0.04，KL_BREAK 0.15→0.075，intent/goal
# TARGET_KL 0.04→0.02，rl-config streamKlCap 0.20→0.10，intent kl_break 0.6→0.3。
KL_BREAK = 0.075
KL_BREAK_CONSEC = 3
ENT_BREAK = 0.60
ENT_BREAK_CONSEC = 8
ENT_BREAK_MAX_WINRATE = 0.5
CIRCUIT_EXIT_CODE = 3


def breaker_update(
    kl_streak: int,
    ent_streak: int,
    *,
    kl: float,
    entropy: float,
    win_rate: float,
    kl_break: float = KL_BREAK,
    kl_consec: int = KL_BREAK_CONSEC,
    ent_break: float = ENT_BREAK,
    ent_consec: int = ENT_BREAK_CONSEC,
    ent_max_winrate: float = ENT_BREAK_MAX_WINRATE,
    ent_peak: float | None = None,
) -> tuple[int, int, str | None]:
    """推进连击计数并判定是否熔断。

    返回 (kl_streak, ent_streak, tripped)：tripped 为 None 表示继续训练，
    否则为可读的熔断原因（调用方写 circuit_break 事件并停车）。
    agg 缺失（流式 checkpoint-complete 轮无任何梯度步）由调用方短路，不计连击。

    kl_break / kl_consec 为可选覆盖（默认 = per-tick 常量 KL_BREAK / KL_BREAK_CONSEC）。
    意图 RL 每代是一次 12–15 局大更新，每代 KL 天然 ≈0.32–0.49（旧 2× 口径；P0-3 后
    新口径 ≈0.16–0.245），远超 per-tick 的 0.045–0.054（旧口径）健康带 → 必须用更高
    阈值（如 0.6 → 新口径 0.3 / 3），否则连续 3 代必误熔断（Bug D）。

    ent_break / ent_consec / ent_max_winrate 同为可选覆盖（课程级可配，见模块 docstring
    的 2026-09-06 修正）。ent_peak = 本轮之前见过的最大熵，None = 无历史基线 → 退回
    绝对电平判定（旧行为）。调用方负责在每轮判定后把 peak 更新为 max(peak, entropy)。
    """
    kl_streak = kl_streak + 1 if kl >= kl_break else 0
    ent_streak = (
        ent_streak + 1
        if _ent_collapsed(
            entropy,
            win_rate,
            ent_break=ent_break,
            ent_max_winrate=ent_max_winrate,
            ent_peak=ent_peak,
        )
        else 0
    )
    if kl_streak >= kl_consec:
        return (
            kl_streak,
            ent_streak,
            f"kl>={kl_break} for {kl_streak} consecutive iters (now {kl:.3f})",
        )
    if ent_streak >= ent_consec:
        return (
            kl_streak,
            ent_streak,
            (
                f"entropy<={ent_break} for {ent_streak} consecutive iters "
                f"(now {entropy:.3f}, winRate={win_rate})"
            ),
        )
    return kl_streak, ent_streak, None


def _ent_collapsed(
    entropy: float,
    win_rate: float,
    *,
    ent_break: float,
    ent_max_winrate: float,
    ent_peak: float | None,
) -> bool:
    """单轮 ENT 崩塌判定（相对崩塌语义，纯函数）。

    ent_peak 为 None → 无历史基线，退回旧的绝对电平判定（entropy <= 阈值）。
    有基线时要求相对崩塌：① 峰值曾高于阈值、现已跌破；② 相对峰值跌幅 ≫ 噪声。
    """
    if entropy > ent_break or win_rate >= ent_max_winrate:
        return False
    if ent_peak is None:
        return True
    return ent_peak > ent_break or (ent_peak - entropy) >= ENT_COLLAPSE_DROP

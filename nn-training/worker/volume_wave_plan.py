"""连续配额的一波规划：**纯函数 + 两个小 dataclass**（P0-1 第五刀从 `loop_volume` 切出，2026-11-28）。

切法 = 「独立所有者 + 独立触发条件」：`plan_continuous_wave` 只吃账本读数（collected /
games_done）+ 常量快照 `ContinuousState`，吐 `WavePlan`；不碰任何 `self`、不写盘、不记日志。
`VolumePlanner` 负责把结果落进槽位与事件；本模块只做算术，因此可以单独喂表测试。

`learned_samples_per_game` 是自校准的除法（本轮已结算的局 → 局均 samples），同样无副作用。
"""

from __future__ import annotations

from dataclasses import dataclass


def learned_samples_per_game(samples: int, games: int) -> int:
    """**本轮自己**已结算的局 → 局均 samples（自校准；`0` = 还学不到/不可用）。

    为什么需要它（plan/rollout-estfree-worksteal §4.1）：est 已从课程/CLI 面退场，首轮连
    历史都没有 ⇒ 第一小批只能「不猜」（按宽度满铺），而**这一批本身就是最好的标定源**：
    下一批起 `collected/games` 就是盘上事实，不需要任何估计。

    口径与 `worker.resume.trailing_samples_per_game` 同：只认已结算 shard 的 `nSamples`
    与**占过名额的局数**（掉局零样本 ⇒ samples=0 ⇒ 返回 0，让调用方继续「不猜」而不是
    拿一个被瞬时掉局拖低的数去反解局数）。纯函数，无 IO。
    """
    g = int(games)
    s = int(samples)
    return max(1, round(s / g)) if g > 0 and s > 0 else 0


@dataclass
class ContinuousState:
    """连续配额一轮的常量快照（循环段与收官段共用；不再靠 20+ 个局部量）。"""

    stages: list[int]
    target: int
    quota: int
    max_batches: int
    width: int
    game_cap: int
    stage_caps: dict[int, int]
    course_fp: str | None
    corpus_fp: str | None
    extra_wver: str | None
    est_global: int
    est_s: dict[int, int]
    start_idx: dict[int, int]
    first_est_used: dict[int, int]
    first_est_raw: dict[int, int]
    first_est_src: dict[int, str]


@dataclass(frozen=True, slots=True)
class WavePlan:
    """一波连续采集的派发表与读数（派发前一次算清，循环只消费）。"""

    games: dict[int, int]
    dispatch_est: dict[int, int]
    raw_est: dict[int, int]
    src: str
    src_by_stage: dict[int, str]
    shortfall: dict[int, int]
    pool_note: str
    capped: bool


def plan_continuous_wave(
    state: ContinuousState,
    collected: dict[int, int],
    games_done: dict[int, int],
    pool_mult: float,
) -> WavePlan:
    """按盘上账本算下一波：est → 候选池（或冷启动满铺）→ 派发表。"""
    from worker.volume_alloc import (
        pick_stage_est,
        pool_games_by_stage,
        pull_est_low,
        spread_games_by_stage,
    )

    # 局均估计（plan/rollout-estfree-worksteal §4.1）：① **本轮自己**已结算的局
    # （自校准 — 冷启动第一小批之后就有值了）；② 前几轮的**盘上历史**（est_s，最近一轮）。
    # 顺序不能反（plan/rollout-corpus-supply §1.3/§3.1 A0-a）：旧代码是
    # `trailing or learned`，而 trailing 永远有值 ⇒ 自校准**恒被短路**，est 一直跟着
    # 历史窗口走（652 vs 实际 530，+23% ⇒ 少派局 ⇒ 多补一批）。
    # 两者都拿不到（首轮第一小批）⇒ **不猜**：按宽度满铺一把（est-free），下一批起重校准。
    est_s_now: dict[int, int] = {}
    est_src_now: dict[int, str] = {}
    for s in state.stages:
        est_s_now[s], est_src_now[s] = pick_stage_est(
            learned_samples_per_game(collected.get(s, 0), games_done.get(s, 0)),
            int(state.est_s.get(s, 0) or 0),
        )
    est_now = int(state.est_global or 0) or learned_samples_per_game(
        sum(collected.values()), sum(games_done.values())
    )
    # 派局用的 est 往低处拉（A0-c）：`ceil(缺口/est)` 因此多派 ~8% 局，把「局长继续变短」
    # 的趋势误差吃掉一部分。只动派局，**不动硬顶**（`stage_caps` 仍由原 est 算）。
    est_dispatch = {s: pull_est_low(v) for s, v in est_s_now.items()}
    est_srcs = set(est_src_now.values())
    est_src = next(iter(est_srcs)) if len(est_srcs) == 1 else "mixed"
    shortfall = {
        s: state.quota - int(collected.get(s, 0))
        for s in state.stages
        if int(collected.get(s, 0)) < state.quota
    }
    if est_now > 0:
        # ★ 2026-10-21（用户口径）：派的是**候选池**，不是"刚好够的局数"。
        #   池 = 缺口 × mult（默认 2）⇒ 队列里一直有活 ⇒ 每个槽从 t=0 起就满负荷；
        #   结算一局就补一局（`on_result` 实时更新 `live`），逐关一满额就 `halt`，
        #   池里剩下的候选**从不运行**。墙钟由「配额达成时刻」决定，与池多大无关，
        #   因此 est 的 ±40% 跳变不再换来「第二批」。
        games = pool_games_by_stage(
            stages=state.stages,
            collected=collected,
            games_done=games_done,
            target_transitions=state.target,
            ests=est_dispatch,
            game_caps=state.stage_caps,
            width=0,  # 池远大于槽位 ⇒ 不需要（也没法）知道集群有几个槽
            mult=pool_mult,
        )
        pool_note = f"池 {sum(games.values())} 局（×{pool_mult}）"
        capped = False
    else:
        # 无估计（首轮无历史）⇒ 「不猜」档：与云机补差**同一个** est-free 原语满铺。
        games = spread_games_by_stage(
            stages=state.stages,
            collected=collected,
            games_done=games_done,
            target_transitions=state.target,
            game_caps=state.stage_caps,
            width=state.width,
        )
        capped = False
        est_src = "cold-start"
        est_src_now = {s: "cold-start" for s in state.stages}
        pool_note = f"池 {sum(games.values())} 局（冷启动按宽度 {state.width} 铺）"
    return WavePlan(
        games=games,
        dispatch_est=est_dispatch,
        raw_est=est_s_now,
        src=est_src,
        src_by_stage=est_src_now,
        shortfall=shortfall,
        pool_note=pool_note,
        capped=capped,
    )


def remember_first_est(state: ContinuousState, plan: WavePlan) -> None:
    """记首波读数：批数由首波决定，记末波的 est 等于「事后诸葛亮」。"""
    if state.first_est_used:
        return
    # 冷启动档（无 learned、无 trailing）根本不是「用了某个估计」：那批是
    # `spread_games_by_stage(width)` 按机器宽度铺的。记 `-1` 而不是 `0`
    # （0 在下游是「无估计」的值，会和「估出来是 0」混成一件事）。
    cold = all(v == "cold-start" for v in plan.src_by_stage.values())
    state.first_est_used = {
        s: (-1 if cold else int(plan.dispatch_est.get(s, 0))) for s in state.stages
    }
    state.first_est_raw = {
        s: (-1 if cold else int(plan.raw_est.get(s, 0))) for s in state.stages
    }
    state.first_est_src = dict(plan.src_by_stage)

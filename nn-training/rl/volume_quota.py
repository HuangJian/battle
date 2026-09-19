"""volume_quota.py — 配额感知连续采集（2026-09-19，用户指令：退役离散补波）。

替代 `volume_waves` 的「初波 G0 + w1/w2 补波」：loop **实时**按各关已结算 samples
差额分配下一小批局数；即将足额（`collected + inflight*est ≥ quota`）则**不再派**；
差额大的关多派。无 wave_idx 语义。

## 量纲（与 volume_waves T9 同一条红线）

`target_transitions` / `est` / `collected` 全部是 **已结算 shard 的 nSamples**，
不是 ticks。

## 种子（§2.3 确定性 + §2.2.5 跨关独立）

`(rotate_seed, it, stage)` 独立流；该关第 `k` 局（0-based 本轮累计派发序号）=
流上第 `k` 次抽签。**不读其它关的流位置** ⇒ 某关多派/少派不改变别关种子。

## 软停 / 硬顶

- 软停：`collected + inflight * est_s ≥ quota` ⇒ 本批该关 0 局（压过冲）。
- 硬顶：`games_done ≥ game_cap` ⇒ 停该关并标 capped（配额未满时响亮）。
- 安全阀：`max_batches`（默认 12）防 est 持续低估时的无限循环——触顶时响亮日志，
  **不是**静默短采。

## 与 §15.5

采样规则相对 wave 版变更 ⇒ **新实验**：fresh `--out/--traj`；旧 wave 语料不可
与本规则混训比较。`VOLUME_RULE_V2` 供血缘指纹。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

#: 连续配额规则版本（进 corpus 血缘；改分配/种子语义 =  bump + 新实验）。
VOLUME_RULE_V2 = 2

#: 与 build_pairs / volume_waves 初波同族 tag（跨关独立流的第 4 键固定 0）。
_CONTINUOUS_TAG = 0x5EED
_SEED_SPACE = 2**30

#: 安全阀：单轮最多派发的小批数（非语义配额，防死循环）。
DEFAULT_MAX_BATCHES = 12

#: 单关局数硬顶默认倍数（相对 ceil(quota/est) 的初值）。
DEFAULT_GAME_CAP_MULT = 4

STOP_QUOTA_MET = "quota_met"
STOP_GAME_CAP = "game_cap"
STOP_BATCH_CAP = "batch_cap"


def _ceil_div(a: int, b: int) -> int:
    return -(-int(a) // int(b))


def target_per_stage(target_transitions: int, n_stages: int) -> int:
    """分关达标线 = ceil(target / n_stages)。"""
    if n_stages <= 0:
        raise ValueError(f"target_per_stage 需要 n_stages ≥ 1，得到 {n_stages}")
    if int(target_transitions) <= 0:
        raise ValueError(f"target_per_stage 需要 target_transitions ≥ 1，得到 {target_transitions}")
    return _ceil_div(target_transitions, n_stages)


def continuous_seed_stream(rotate_seed: int, it: int, stage_id: int) -> np.random.Generator:
    """`(rotate_seed, it, stage)` 独立流（跨关不共享抽签位置）。"""
    return np.random.default_rng(
        [int(rotate_seed), _CONTINUOUS_TAG, int(it), int(stage_id), 0]
    )


def stage_seeds(rotate_seed: int, it: int, stage_id: int, start_idx: int, n: int) -> list[int]:
    """该关本轮第 `[start_idx, start_idx+n)` 局的 seed（确定性、可续跑）。"""
    count = int(n)
    if count <= 0:
        return []
    start = max(0, int(start_idx))
    stream = continuous_seed_stream(rotate_seed, it, stage_id)
    draws = stream.integers(1, _SEED_SPACE, size=start + count)
    return [int(s) for s in draws[start : start + count]]


def default_game_cap(quota: int, est: int) -> int:
    """默认单关局数硬顶 ≈ 初值局数 × MULT（est≤0 时仅 quota 保护）。"""
    est = max(1, int(est))
    g0 = max(1, _ceil_div(int(quota), est))
    return max(1, g0 * DEFAULT_GAME_CAP_MULT)


def allocate_stage_games(
    *,
    collected: int,
    inflight: int,
    quota: int,
    est: int,
    games_done: int,
    game_cap: int,
) -> tuple[int, str | None]:
    """本批该关应派局数 + 停因（None=可继续派）。

    软停：`collected + inflight*est ≥ quota` → (0, None) 已「预计」达标。
    硬顶：`games_done ≥ game_cap` → (0, game_cap)。
    """
    got = int(collected)
    inf = max(0, int(inflight))
    q = int(quota)
    e = max(1, int(est))
    done = int(games_done)
    cap = int(game_cap)
    if got >= q:
        return 0, STOP_QUOTA_MET
    if cap > 0 and done >= cap:
        return 0, STOP_GAME_CAP
    projected = got + inf * e
    if projected >= q:
        return 0, None  # 软停：在飞局预计能补齐
    remaining = q - projected
    n = _ceil_div(remaining, e)
    if cap > 0:
        room = cap - done - inf
        if room <= 0:
            return 0, STOP_GAME_CAP
        n = min(n, room)
    return max(0, n), None


@dataclass(frozen=True)
class ContinuousPlan:
    """一小批连续采集计划。空 `games_by_stage` = 本轮不再派。"""

    games_by_stage: dict[int, int] = field(default_factory=dict)
    stopped: dict[int, str] = field(default_factory=dict)
    shortfall: dict[int, int] = field(default_factory=dict)
    projected_shortfall: dict[int, int] = field(default_factory=dict)
    capped: bool = False

    @property
    def games_total(self) -> int:
        return sum(self.games_by_stage.values())


def plan_continuous_batch(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    inflight: Mapping[int, int],
    target_transitions: int,
    ests: Mapping[int, int],
    games_done: Mapping[int, int],
    game_cap: int = 0,
    fallback_est: int = 1,
) -> ContinuousPlan:
    """按**当前账本 + 在飞**决定下一批各关局数（差额大者多派；足额则 0）。"""
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("plan_continuous_batch 需要至少一个 stage")
    quota = target_per_stage(target_transitions, len(stage_list))
    games_by_stage: dict[int, int] = {}
    stopped: dict[int, str] = {}
    shortfall: dict[int, int] = {}
    proj_short: dict[int, int] = {}
    capped = False
    for stage in stage_list:
        got = int(collected.get(stage, 0))
        inf = max(0, int(inflight.get(stage, 0)))
        done = int(games_done.get(stage, 0))
        est = int(ests.get(stage, 0) or fallback_est)
        if est <= 0:
            est = max(1, int(fallback_est))
        short = max(0, quota - got)
        shortfall[stage] = short
        proj_short[stage] = max(0, quota - got - inf * est)
        n, why = allocate_stage_games(
            collected=got,
            inflight=inf,
            quota=quota,
            est=est,
            games_done=done,
            game_cap=game_cap,
        )
        if why == STOP_GAME_CAP:
            capped = True
        if n <= 0:
            if why:
                stopped[stage] = why
            elif short > 0:
                stopped[stage] = "soft_hold"  # 在飞预计补齐，本批不派
            continue
        games_by_stage[stage] = n
    return ContinuousPlan(
        games_by_stage=games_by_stage,
        stopped=stopped,
        shortfall=shortfall,
        projected_shortfall=proj_short,
        capped=capped,
    )


def continuous_pairs(
    rotate_seed: int,
    it: int,
    games_by_stage: Mapping[int, int],
    start_idx: Mapping[int, int],
) -> list[tuple[int, int]]:
    """计划 → `(stage, seed)`（stage 升序；各关从 start_idx 起连续抽签）。"""
    pairs: list[tuple[int, int]] = []
    for stage in sorted(int(s) for s in games_by_stage):
        n = int(games_by_stage[stage])
        start = int(start_idx.get(stage, 0))
        pairs.extend((stage, sd) for sd in stage_seeds(rotate_seed, it, stage, start, n))
    return pairs

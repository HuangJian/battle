"""volume_alloc.py — 分关采样分配（plan/rollout-stage-balance.plan.md §3，`VOLUME_ALLOC_RULE`）。

为什么存在：`target_transitions` 的达标线是**分关** quota（`ceil(target/关数)`），而云机腿
过去按**全局** est 反解**每关同一个**局数（`volume_waves.initial_games`）⇒ 关间 `samples/局`
差 1.75× 时，同一轮里既有浪费（长局关溢出被裁）又有缺口（短局关欠采）。本模块把「每关多少局」
拆成两个纯函数：**首批按上界反解**（故意偏小 ⇒ 过采率低）、**补差按实测均值 + 有界多批**
（缺口不可恢复、浪费可恢复 ⇒ 末批偏保守）。

标定（真机 `tmp/x21-psh-b` 142 轮 per-game，bootstrap 2×10⁴ 轮；配方见评审 §7）：

| 规则 | P(本轮有缺口) | E[局数/轮] | E[缺口样本] | E[浪费样本] |
|---|---|---|---|---|
| 全局 est 一批，无补差 | 91.1% | 157.8 | 1918 | 2659 |
| 上界首批 + **1** 批补差（均值） | 71.6% | 160.1 | 299 | 1761 |
| 上界首批 + ≤4 批（…, 末批偏保守）= **本模块口径** | **3.0%** | 162.0 | **2** | 2056 |

量纲（与 `volume_waves` 文件头 T9 同一条红线）：`target/quota/collected/est_*` 全是
**已结算 shard 的 `nSamples` 之和**（samples，不是 ticks）。节点侧补差的分母取**产出集**
（未裁剪：it168 实测 Σ=48 080 > kept 44 747），裁剪后的数会让估计量随配额有偏。

本模块只有纯函数与常量（无 IO、无 torch、无随机）；IO（扫 shard 目录、跑子进程）在
`worker/iter_topup.py`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from worker.volume_quota import target_per_stage

#: 分配规则版本（进 iteration 事件 / 云机轮报的 provenance；**不进** `corpus_identity_fp`——
#: 与 `est` 同类：都是「est 决定的局数」这类运行参数，本机链早就逐轮变而不进身份）。
VOLUME_ALLOC_RULE = "per-stage-v3"

#: 首批「上界」系数：`est_hi = ceil(est × 1.15)` ⇒ 首批局数偏少 ⇒ 过采率 10.6%。
#: 1.25 会把过采率降到 3.1%，代价是首批更小、补差批数变多——回标值（§3.4 表）。
DEFAULT_EST_HI_FACTOR = 1.15

#: 补差批数上界（**不含**首批）：首批 + 最多 3 批补差 ⇒ P(缺口) 18.9% → 3.0%（§3.4 表）。
DEFAULT_TOPUP_MAX_BATCHES = 3

#: 末批（`batches_left == 1`）的 est 折扣：缺口不可恢复、浪费可恢复 ⇒ 末批故意偏保守。
DEFAULT_LAST_BATCH_LO_FACTOR = 0.85


def _ceil_div(a: int, b: int) -> int:
    return -(-int(a) // int(b))


def _scale(x: int, factor: float) -> tuple[int, int]:
    """`(x × factor × 1000, 1000)`——**定点化**，避免二进制浮点把「恰好整数的界」抬一档。

    为什么不让调用方写 `math.ceil(est * 1.15)`：`20 × 1.15` 在浮点里是 22.999999999999996
    或 23.000000000000004（取决于值），`ceil` 在后者上会多算一局；而这一局是全链路的基准
    单位（局数硬顶、目录序号、配额反解都靠它）。因子精度取 3 位小数（1.15 / 0.85 都精确）。
    """
    return int(x) * round(float(factor) * 1000), 1000


def est_hi(est: int, *, factor: float = DEFAULT_EST_HI_FACTOR) -> int:
    """`est` 的上界估计 `ceil(est × factor)`（≤0 一律响亮报错：没有估计值就是乱采）。"""
    if int(est) <= 0:
        raise ValueError(f"est_hi 需要 est ≥ 1，得到 {est!r}")
    if float(factor) <= 0:
        raise ValueError(f"est_hi 需要 factor > 0，得到 {factor!r}")
    num, den = _scale(int(est), factor)
    return max(1, _ceil_div(num, den))


def alloc_games_by_stage(
    stages: Sequence[int],
    target_transitions: int,
    *,
    ests_hi: Mapping[int, int],
) -> dict[int, int]:
    """首批：`G_s = max(1, ceil(quota / est_hi_s))`（**逐关独立**，跨关不借额度）。"""
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("alloc_games_by_stage 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    out: dict[int, int] = {}
    for stage in stage_list:
        hi = int(ests_hi.get(stage, 0) or 0)
        if hi <= 0:
            raise ValueError(f"alloc_games_by_stage 缺 stage {stage} 的 est_hi（收到 {ests_hi!r}）")
        out[stage] = max(1, _ceil_div(quota, hi))
    return out


def topup_games_by_stage(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    games_done: Mapping[int, int],
    target_transitions: int,
    ests: Mapping[int, int],
    game_caps: Mapping[int, int],
    batches_left: int,
    last_lo_factor: float = DEFAULT_LAST_BATCH_LO_FACTOR,
) -> dict[int, int]:
    """补差批：逐关 `max(0, ceil((quota − collected) / est))`，`game_cap` 截断。

    * 已达标的关**不进结果**（`collected ≥ quota`）；
    * 触 `game_cap`（`games_done ≥ cap`）的关**不进结果**（调用方负责把「未达标 + 触顶」
      当响亮事件——静默短采正是本模块要防的事）；
    * `batches_left <= 1` ⇒ 本批是最后一批 ⇒ est 打 `last_lo_factor` 折（偏保守：多跑
      几局换「不缺口」，代价有上界）。
    * 返回空 dict ⇒ 本轮不再补差。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("topup_games_by_stage 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    last = int(batches_left) <= 1
    out: dict[int, int] = {}
    for stage in stage_list:
        got = int(collected.get(stage, 0) or 0)
        done = int(games_done.get(stage, 0) or 0)
        if got >= quota:
            continue
        cap = int(game_caps.get(stage, 0) or 0)
        if cap > 0 and done >= cap:
            continue
        est = int(ests.get(stage, 0) or 0)
        if est <= 0:
            raise ValueError(f"topup_games_by_stage 缺 stage {stage} 的 est（收到 {ests!r}）")
        if last:
            num, den = _scale(est, last_lo_factor)
            est = max(1, num // den)  # 向下取整 = 偏保守方向
        remaining = quota - got
        n = _ceil_div(remaining, est)
        if cap > 0:
            n = min(n, max(0, cap - done))
        if n <= 0:
            continue
        out[stage] = n
    return out


def shortfall_by_stage(
    *, stages: Sequence[int], collected: Mapping[int, int], target_transitions: int
) -> dict[int, int]:
    """逐关缺口（`max(0, quota − collected)`；已达标的关不在结果里）。**硬指标**。"""
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("shortfall_by_stage 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    out: dict[int, int] = {}
    for stage in stage_list:
        short = quota - int(collected.get(stage, 0) or 0)
        if short > 0:
            out[stage] = short
    return out


def wasted_samples(
    *, stages: Sequence[int], collected: Mapping[int, int], target_transitions: int
) -> int:
    """过采样本数（`Σ max(0, collected − quota)`）。**软指标**：有上界即可，不要求 0。"""
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("wasted_samples 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    return sum(max(0, int(collected.get(stage, 0) or 0) - quota) for stage in stage_list)


def stage_totals(
    manifests: Sequence[object],
) -> tuple[dict[int, int], dict[int, int]]:
    """单局 shard manifest 列表 → `(collected_by_stage, games_by_stage)`（**产出集**口径）。

    只读 `stage` + `nSamples`（回退 `totalSamples`）；缺 stage / 缺样本数的行跳过
    （半个目录 / 坏 manifest 不该让整轮补差失败——它只是少一局的读数，不静默改口径）。
    """
    collected: dict[int, int] = {}
    games: dict[int, int] = {}
    for m in manifests:
        if not isinstance(m, Mapping):
            continue
        raw_stage = m.get("stage")
        if isinstance(raw_stage, bool) or not isinstance(raw_stage, int):
            continue
        n = m.get("nSamples")
        if not isinstance(n, int) or isinstance(n, bool):
            n = m.get("totalSamples")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            continue
        collected[int(raw_stage)] = collected.get(int(raw_stage), 0) + int(n)
        games[int(raw_stage)] = games.get(int(raw_stage), 0) + 1
    return collected, games


def default_game_caps(
    stages: Sequence[int],
    target_transitions: int,
    *,
    ests: Mapping[int, int],
    explicit: int = 0,
    mult: int = 4,
) -> dict[int, int]:
    """逐关局数硬顶：`explicit > 0` 时全关同值；否则 `mult × ceil(quota / est_s)`。

    ★ 与 `volume_quota.default_game_cap` 的分工：那个是**单一 est** 的旧口径（本机链历史上
    用全局 est 算 cap），本函数是**分关**版——低 est 的关需要更多局，用全局 est 会让它
    「配额未满就触顶」（评审 P1-5），而 DoD 又把「触 game_cap」当合格路径 ⇒ 静默借口。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("default_game_caps 需要至少一个 stage")
    if int(mult) <= 0:
        raise ValueError(f"default_game_caps 需要 mult ≥ 1，得到 {mult!r}")
    if int(explicit) > 0:
        return {stage: int(explicit) for stage in stage_list}
    quota = target_per_stage(int(target_transitions), len(stage_list))
    out: dict[int, int] = {}
    for stage in stage_list:
        est = int(ests.get(stage, 0) or 0)
        if est <= 0:
            raise ValueError(f"default_game_caps 缺 stage {stage} 的 est（收到 {ests!r}）")
        out[stage] = max(1, _ceil_div(quota, est) * int(mult))
    return out


def validate_volume_block_ext(block: object, *, require_runtime: bool = False) -> dict:
    """分关 `volume` 块（计划侧 + 可选运行时 `it`/`rotate_seed`）的形状 + **同源**校验。

    同源 = 「块里的 `per_stage_quota` 与 `games_per_stage_by_stage` 必须能从 `target/stages/est`
    重新解出来」——两侧（hub 导出 / 节点补差）各自只信自己重算的那一份，杜绝「规则改了但块
    没跟上」的静默漂移。失败抛 `ValueError`（调用方转 `ProtocolError`，响亮失败）。
    """
    if not isinstance(block, dict):
        raise ValueError(f"volume 必须是对象，收到 {type(block).__name__}")
    required: tuple[str, ...] = (
        "target_transitions",
        "stages",
        "per_stage_quota",
        "games_per_stage_by_stage",
        "est_s_by_stage",
        "est_hi_factor",
        "topup",
    )
    if require_runtime:
        required = (*required, "it", "rotate_seed")
    missing = [k for k in required if k not in block]
    if missing:
        raise ValueError(f"volume 缺字段: {missing}")
    int_keys = ("target_transitions", "per_stage_quota", "it", "rotate_seed") if require_runtime else (
        "target_transitions",
        "per_stage_quota",
    )
    for k in int_keys:
        v = block[k]
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"volume.{k} 必须是正整数，收到 {v!r}")
    raw_stages = block["stages"]
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ValueError(f"volume.stages 必须是非空数组，收到 {raw_stages!r}")
    stages: list[int] = []
    for s in raw_stages:
        if isinstance(s, bool) or not isinstance(s, int):
            raise ValueError(f"volume.stages 必须是整数数组，收到 {s!r}")
        stages.append(int(s))
    ests: dict[int, int] = {}
    raw_ests = block["est_s_by_stage"]
    if not isinstance(raw_ests, dict):
        raise ValueError(f"volume.est_s_by_stage 必须是对象，收到 {raw_ests!r}")
    for k, v in raw_ests.items():
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"volume.est_s_by_stage[{k!r}] 必须是正整数，收到 {v!r}")
        ests[int(k)] = int(v)
    missing_ests = [s for s in stages if s not in ests]
    if missing_ests:
        raise ValueError(f"volume.est_s_by_stage 缺关: {missing_ests}")
    raw_g = block["games_per_stage_by_stage"]
    if not isinstance(raw_g, dict):
        raise ValueError(f"volume.games_per_stage_by_stage 必须是对象，收到 {raw_g!r}")
    games: dict[int, int] = {}
    for k, v in raw_g.items():
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"volume.games_per_stage_by_stage[{k!r}] 必须是正整数，收到 {v!r}")
        games[int(k)] = int(v)
    missing_g = [s for s in stages if s not in games]
    if missing_g:
        raise ValueError(f"volume.games_per_stage_by_stage 缺关: {missing_g}")
    extra_g = [s for s in games if s not in stages]
    if extra_g:
        raise ValueError(f"volume.games_per_stage_by_stage 多出关: {extra_g}")
    factor = block["est_hi_factor"]
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or float(factor) <= 0:
        raise ValueError(f"volume.est_hi_factor 必须是正数，收到 {factor!r}")
    want_quota = target_per_stage(int(block["target_transitions"]), len(stages))
    if want_quota != int(block["per_stage_quota"]):
        raise ValueError(
            f"volume.per_stage_quota={block['per_stage_quota']} 与 ceil(target/关数)={want_quota}"
            " 不符（分关达标线两侧必须同源）"
        )
    want_g = alloc_games_by_stage(
        stages,
        int(block["target_transitions"]),
        ests_hi={s: est_hi(ests[s], factor=float(factor)) for s in stages},
    )
    for stage in stages:
        if games[stage] != want_g[stage]:
            raise ValueError(
                f"volume.games_per_stage_by_stage[{stage}]={games[stage]} 与"
                f" ceil(达标线/est_hi)={want_g[stage]} 不符（首批局数两侧必须同源）"
            )
    raw_topup = block["topup"]
    if not isinstance(raw_topup, dict):
        raise ValueError(f"volume.topup 必须是对象，收到 {raw_topup!r}")
    for k in ("enabled", "max_batches", "last_lo_factor", "max_games_per_stage"):
        if k not in raw_topup:
            raise ValueError(f"volume.topup 缺字段: {k!r}")
    if not isinstance(raw_topup["enabled"], bool):
        raise ValueError(f"volume.topup.enabled 必须是布尔，收到 {raw_topup['enabled']!r}")
    for k in ("max_batches", "max_games_per_stage"):
        v = raw_topup[k]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"volume.topup.{k} 必须是非负整数，收到 {v!r}")
    lo = raw_topup["last_lo_factor"]
    if isinstance(lo, bool) or not isinstance(lo, (int, float)) or not (0 < float(lo) <= 1):
        raise ValueError(f"volume.topup.last_lo_factor 必须在 (0, 1]，收到 {lo!r}")
    out = dict(block)
    out["stages"] = stages
    out["est_s_by_stage"] = ests
    out["games_per_stage_by_stage"] = games
    out["est_hi_factor"] = float(factor)
    return out


def validate_runtime_volume(block: object) -> dict:
    """运行时块校验（= 计划块校验 + `it`/`rotate_seed` 两个必填运行时键）。"""
    return validate_volume_block_ext(block, require_runtime=True)

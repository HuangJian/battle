"""volume_waves.py —— 按样本量动态采集的纯逻辑（plan/dynamic-rollout-volume.plan.md §2，P0）。

课程用 `target_transitions`（/轮）代替固定局数做采集配额：初波按目标量反解每关局数，
结算后按**已结算 transitions** 逐关补波，直到达标或触硬顶。

本模块只有纯函数与常量（无 IO、无 torch）：配额数学、波次种子流、终止谓词、补波计划。
shard 账本读取在 `biz/resume.py`（settled_stage_totals / trailing_samples_per_game），
loop 接线在 `trainer/loop_volume.py::_volume_topup`（S4 第十八刀从 `loop_core` 搬来）。

## 量纲（★ 2026-09-15 T9 定案；改这条 = 新实验 §15.5）

`target_transitions`（分子）与 `est_samples_per_game`（分母）**必须是同一个单位**：
**已结算 shard 的 `nSamples` 之和**（= 决策步数，PPO 真正吃的东西）。
**不是 ticks**：exporter 按 K 降采样（x3-power 实测 samples/ticks ≈ 0.1007），ticks 只是
clocks。旧键名 `est_ticks_per_game` 正是把 ticks 填进 samples 分母 —— 10× 误采：
target=600000 / 4 关 / est=980（ticks）时初波只采到目标的 ~⅒，3 波补波后仍停在 ~35%
触 `wave_cap`（结课评审复算的「兑现 37% 触顶」用例）。键名改后换算：
`est_samples_per_game = 局均 ticks × (samples/ticks)`（x3：980 × 0.1007 ≈ 98.7 ⇒ 取 99），
或等价地 `局均 ticks / K`（K = 降采样倍数；x3 实测 K ≈ 10 ⇒ 980 ticks ≈ 98 samples）。
⚠ 自动 est（`trailing_samples_per_game`）也必须读 jsonl 的 `samples` 字段，**不能读
`ticks`**——否则首轮正确、第二轮起又变成 10×（这是同一条 bug 的另一半）。

## 语义要点（改动任何一条 = 新实验，§15.5）

1. **缺席即老行为**：课程不写 `target_transitions`（=0）时本模块一个函数都不被调用，
   `build_pairs` 输出与今日逐字节一致（回归单测钉死）。
2. **分关配额**：达标线 = `ceil(target / n_stages)`，逐关独立判定——短局关淹不了长局关
   （x3 acd 733t vs abd 989t 差 35%，就是前车）。
3. **种子流按 (stage, wave) 独立**：给 A 关加波绝不改变 B 关任何一局的种子。
   这是与「单条顺序流」的关键分歧：计划 §2.2.1 说初波「种子流与今日 `build_pairs`
   同键」——此处取**同键族**（同 rotate_seed / it / 同 tag 0x5EED），但按 stage 拆成
   独立流。理由：单流下「每关抽几签」会随 est/配额变化而移动**后续关**的流位置，
   §2.2.5 的跨关独立性不变量随之失守。带 key 的课程本来就是新实验（§15.5 要求
   fresh `--out/--traj`），不需要与老流逐字节同构；不带 key 的课程走 `build_pairs` 原路。
4. **波次种子标签**：初波（wave_idx=0）用 `0x5EED`（与 `build_pairs` 显式轮转路径同
   tag，便于血缘识别）；补波（wave_idx≥1）用 `0xAA9E`。计划里写作 `0xWA9E`——那不是
   合法十六进制字面量，此处取其助记意图（"W" 版 tag）。
5. **掉局不计、超时局计入**：掉局零样本（天然触发补采，这是特性）；超时局的 transitions
   是真实 on-policy 数据。
6. **可 replay，而非重新抛硬币**：种子按 (rotate_seed, it, stage, wave) 键控 ⇒ 同一波次
   同一对局表。但 `wave_idx` 本身是**决策而不是账本的函数**（它同时是种子流的键）——
   只按账本重算会在重启后把计数器拉回 1，用 wave-1 的种子去补 wave-3 的缺口（同观测史、
   不同波次序列）。因此续跑靠 commit journal：预算按 WAL 续算（跨重启**不重领额度**），
   停在波中（有 start 无 finish）的那一波按 WAL 的对局表**原样重放**（`parse_wave_records`）。
   2026-09-15 e2e（`e2e/test_volume_e2e.py::test_cross_restart_same_pairs_no_reroll`）证伪了
   本模块首版的「纯函数就够、无需 WAL」假说。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: 规则版本常量——进 `corpus_identity_fp`（采样规则变更 = 新实验，§15.5）。
# 2026-09-30：常量随语料身份下沉 `biz/corpus_fp.py`（`corpus_identity_fp` 要用它，
# 而本模块搬进 worker/ ⇒ 反向 import 是向下边 ✓）。名字保留 ⇒ 调用点不变。
from biz.corpus_fp import VOLUME_RULE_V1 as VOLUME_RULE_V1
from worker.volume_math import GamesByStageMixin
from worker.volume_math import ceil_div as _ceil_div

#: 每轮每关波次数上限（防长尾抖动：补波收益递减，3 波够抹平 est 偏差）。
#: ⚠ 3 波只够抹平「est 偏差」，不够抹平「est 量纲错」——量纲错是 10×，
#: 补波的绝对量也按同一个错估缩放，永远追不上（见文件头量纲节）。
DEFAULT_MAX_WAVES = 3

#: 单关单轮局数硬顶的默认倍数：默认 = 初波 G0 × 本值。
DEFAULT_GAME_CAP_MULT = 4

#: 这里**故意没有** `est_samples_per_game` 的绝对下限（2026-09-15 回退 P2-b）。
#:
#: 曾加过 `MIN_EST_SAMPLES_PER_GAME = 100`，动机：`cap = G0 × 4` 与
#: `G0 = ceil(target/关数/est)` 同源，est 越小两者一起放大，"硬顶"追不上 G0。
#: **但那个前提是错的** —— 它假定 est 必然是三位数、`est < 100` 只能是故障值。
#: `e2e/test_volume_e2e.py::test_quota_converges_within_one_wave` 用 **est=20 的合法小值**
#: 证伪了它：钳到 100 后 `G0 = ceil(100/100) = 1`，初波 1 局/关补不到达标线，
#: 集成测试当场红（该测试的契约就是 `G0 = ceil(每关目标/est) = 5`）。
#: ⇒ 配额反解的数学**保持计划 §2.2.1 原状**；`est ≤ 0` 已由各自的 ValueError 拦下。
#: 若真在意"est 配得过小导致 G0 爆炸"，正确位置是**配置层校验**（CourseConfig 解析时
#: 判 est 与关卡量级是否相称），而不是在纯函数里一刀切 —— 那会连带改掉合法路径的行为。

#: 初波种子流 tag（= build_pairs 显式轮转路径的 0x5EED）。
_INITIAL_WAVE_TAG = 0x5EED
#: 补波种子流 tag（计划 §2.2.5 的 `0xWA9E` → 合法字面量）。
_TOPUP_WAVE_TAG = 0xAA9E
#: seed 抽取上界（与 build_pairs：`integers(1, 2**30)` 同口径）。
_SEED_SPACE = 2**30

#: 停因（`terminate` 的返回值；None = 继续补波）。
STOP_QUOTA_MET = "quota_met"  # 配额达成
STOP_GAME_CAP = "game_cap"  # 触 max_games_per_stage 硬顶（配额未满，需响亮日志）
STOP_WAVE_CAP = "wave_cap"  # 触波次上限（配额未满）


def parse_stages_arg(raw: str) -> list[int]:
    """`--stages` 字符串 → 关号列表（**共享解析器**）。

    抽出来是为了消除「同一份 `--stages` 被两处各解析一遍」的漂移风险：
      · `loop_volume._volume_stages()`（采集侧）：解析失败**响亮 SystemExit**；
      · `loop_steps._per_stage_quota()`（训练侧）：解析失败**静默返 0**（= 全收 = 老行为）。
    两者对同一输入的处理策略不同是**有意的**（采集侧必须响亮，训练侧缺省即老行为），
    但「什么算可解析」必须是同一个判断——否则会出现「采集说合法、训练说非法」的裂缝。

    ⚠ 空串 / 不可解析 ⇒ `ValueError`（**不返回空列表**）：调用方据此区分
    「没给」与「给了坏值」，空列表会让 `target_per_stage` 的 n_stages≤0 分支报错在更远处。
    """
    from biz.course import parse_range

    text = str(raw or "").strip()
    if not text:
        raise ValueError("--stages 为空")
    return parse_range(text)


def target_per_stage(target_transitions: int, n_stages: int) -> int:
    """分关达标线 = ceil(target / n_stages)（n_stages ≤ 0 响亮报错）。

    P2-6（2026-09-15）：`target_transitions ≤ 0` 也响亮报错。原先 `max(0, target)`
    把 0 静默变成「达标线 0」⇒ 每关第一波就 `quota_met` 立即停，采集量掉到 G0
    而不报错——是那种最难发现的静默失效。开动态采集的前提就是 target > 0
    （`loop_volume._volume_active` 同口径），所以这里报错不会误伤老课程：
    老课程根本不会调到本函数。
    """
    if n_stages <= 0:
        raise ValueError(f"target_per_stage 需要 n_stages ≥ 1，得到 {n_stages}")
    if int(target_transitions) <= 0:
        raise ValueError(
            f"target_per_stage 需要 target_transitions ≥ 1，得到 {target_transitions}"
            "（≤0 会让每关立即 quota_met 静默停采；动态采集应确保 target > 0）"
        )
    return _ceil_div(int(target_transitions), int(n_stages))


def initial_games(target_transitions: int, n_stages: int, est_samples_per_game: int) -> int:
    """初波每关局数 `G0 = max(1, ceil(target / n_stages / est))`（计划 §2.2.1）。

    `est_samples_per_game` = **samples/局**（= 局均 ticks / K；见文件头量纲节），
    与 `target_transitions` 同单位。**`≤ 0` = 无估计**（est 已从课程/CLI 面退场，首轮无历史）
    ⇒ 返回 **1 局/关**（不猜）：缺口交给 est-free 的满铺补差批
    （plan/rollout-estfree-worksteal §4.1 冷启动表）。
    **est 不被钳到任何下限**（2026-09-15 回退 P2-b；理由见文件头那段长注释）。
    """
    if est_samples_per_game <= 0:
        return 1
    return max(
        1,
        _ceil_div(
            target_per_stage(target_transitions, n_stages),
            int(est_samples_per_game),
        ),
    )


def topup_games(remaining_transitions: int, est_samples_per_game: int) -> int:
    """补波大小 = `ceil(剩余 / est)`；剩余 ≤ 0 → 0（无波可补）。

    与 `initial_games` 同口径（est = samples/局）；est **不钳**（见文件头长注释）。
    """
    if est_samples_per_game <= 0:
        raise ValueError(f"topup_games 需要 est_samples_per_game ≥ 1，得到 {est_samples_per_game}")
    remaining = int(remaining_transitions)
    if remaining <= 0:
        return 0
    return _ceil_div(remaining, int(est_samples_per_game))


def default_game_cap(initial_g0: int) -> int:
    """默认单关局数硬顶 = 初波 × `DEFAULT_GAME_CAP_MULT`（≥1）。"""
    return max(1, int(initial_g0) * DEFAULT_GAME_CAP_MULT)


def wave_seed_stream(
    rotate_seed: int, it: int, stage_id: int, wave_idx: int
) -> np.random.Generator:
    """`(rotate_seed, it, stage_id, wave_idx)` → 该关该波的**独立**种子流（§2.2.5）。

    独立 = 任何其它关/其它波的抽取都不影响本流（跨关独立性不变量的实现基础）；
    同一个四元组跨进程、跨重启逐字节一致。
    """
    tag = _INITIAL_WAVE_TAG if int(wave_idx) <= 0 else _TOPUP_WAVE_TAG
    return np.random.default_rng([int(rotate_seed), tag, int(it), int(stage_id), int(wave_idx)])


def wave_seeds(rotate_seed: int, it: int, stage_id: int, wave_idx: int, n: int) -> list[int]:
    """该关该波抽 `n` 个 seed（`[1, 2**30)`，与 build_pairs 同值域）；`n ≤ 0` → []。"""
    count = int(n)
    if count <= 0:
        return []
    stream = wave_seed_stream(rotate_seed, it, stage_id, wave_idx)
    return [int(s) for s in stream.integers(1, _SEED_SPACE, size=count)]


def terminate(
    *,
    collected: int,
    target: int,
    waves_done: int,
    max_waves: int,
    games_done: int,
    max_games_per_stage: int,
) -> str | None:
    """单关终止谓词：None = 继续补波，否则返回停因（见 STOP_* 常量）。

    判定顺序（配额达成永远优先——同时触顶但已达标不是「未满」事件）：
    ① 配额达成 → quota_met；② 局数硬顶 → game_cap；③ 波次上限 → wave_cap。
    """
    if int(collected) >= int(target):
        return STOP_QUOTA_MET
    if max_games_per_stage > 0 and int(games_done) >= int(max_games_per_stage):
        return STOP_GAME_CAP
    if int(waves_done) >= int(max_waves):
        return STOP_WAVE_CAP
    return None


@dataclass(frozen=True)
class TopUpPlan(GamesByStageMixin):
    """一次补波计划（`plan_topup` 的产出；空 `games_by_stage` = 本轮不再补波）。"""

    #: 本次要跑的波序号（0-based；初波 = 0，由调用方在首派后传 1 进来补波）。
    wave_idx: int
    #: stage → 本次要派的局数（只含仍需补波的关）。
    games_by_stage: dict[int, int] = field(default_factory=dict)
    #: stage → 停因（quota_met / game_cap / wave_cap）。
    stopped: dict[int, str] = field(default_factory=dict)
    #: stage → 未达标的 transitions 缺口（0 = 已达标）。
    shortfall: dict[int, int] = field(default_factory=dict)
    #: 任一关因硬顶停且配额未满（调用方响亮日志 + iteration 事件打标）。
    capped: bool = False


def plan_topup(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    target_transitions: int,
    est_samples_per_game: int,
    waves_done: int,
    games_done: Mapping[int, int],
    max_waves: int = DEFAULT_MAX_WAVES,
    max_games_per_stage: int = 0,
    initial_g0: int = 0,
) -> TopUpPlan:
    """按各关已结算 transitions 决定下一波（§2.2.3/2.2.4）。

    逐关独立：`shortfall = target_per_stage - collected`；已达标的关进 `stopped`
    且不占波次预算；未达标的关按 `ceil(shortfall / est)` 补一波，并在**不越硬顶**
    的前提下截断（计划要求硬顶是硬顶，不是「下一波才停」）。`max_games_per_stage=0`
    → 用 `default_game_cap(initial_g0)`。
    `collected`（账本 nSamples 之和）与 `est_samples_per_game` 同单位（见文件头量纲节）。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("plan_topup 需要至少一个 stage")
    per_stage = target_per_stage(target_transitions, len(stage_list))
    cap = (
        int(max_games_per_stage)
        if max_games_per_stage > 0
        else (default_game_cap(initial_g0) if initial_g0 > 0 else 0)
    )
    games_by_stage: dict[int, int] = {}
    stopped: dict[int, str] = {}
    shortfall: dict[int, int] = {}
    capped = False
    for stage in stage_list:
        got = int(collected.get(stage, 0))
        done = int(games_done.get(stage, 0))
        short = max(0, per_stage - got)
        shortfall[stage] = short
        why = terminate(
            collected=got,
            target=per_stage,
            waves_done=waves_done,
            max_waves=max_waves,
            games_done=done,
            max_games_per_stage=cap,
        )
        if why == STOP_GAME_CAP:
            capped = True
        if why is not None:
            stopped[stage] = why
            continue
        games = topup_games(short, est_samples_per_game)
        if cap > 0:
            # 硬顶是硬顶：本波也不许越（越界就是「下波才停」的软顶）。
            room = cap - done
            if games > room:
                games = room
            if games <= 0:
                stopped[stage] = STOP_GAME_CAP
                capped = True
                continue
        games_by_stage[stage] = games
    return TopUpPlan(
        wave_idx=int(waves_done),
        games_by_stage=games_by_stage,
        stopped=stopped,
        shortfall=shortfall,
        capped=capped,
    )


@dataclass(frozen=True)
class WaveRecord:
    """本迭代某一波的 WAL 决策记录（补波决策的「不重新抛硬币」载体）。"""

    wave_idx: int
    #: stage → 该波决定的局数（journal 里以字符串键存，此处已转回 int）。
    games: dict[int, int]
    #: 该波是否已闭环（start 之后见到 finish）。未闭环 = 进程停在波次中间。
    finished: bool


#: commit journal 里本模块使用的相位名（与 `loop_volume` 的 `_volume_topup` 同一常量）。
WAVE_PHASE = "volume_wave"


def wave_round_key(it: int, wave_idx: int) -> str:
    """本迭代第 `wave_idx` 波的 WAL round 键（`"<it>:w<k>"`）。"""
    return f"{int(it)}:w{int(wave_idx)}"


def _parse_round_key(round_key: str) -> tuple[int, int] | None:
    """`"7:w2"` → (7, 2)；不含 `:w` 或非数字 → None（别的相位/坏行）。"""
    head, sep, tail = round_key.partition(":w")
    if not sep:
        return None
    try:
        return int(head), int(tail)
    except ValueError:
        return None


def parse_wave_records(lines: Iterable[str], it: int) -> dict[int, WaveRecord]:
    """commit journal 行 → 本迭代各波的决策（wave_idx → WaveRecord）。

    纯函数（吃字符串行，不碰盘）——重启续跑的判据全部可单测。语义与
    `CommitJournal.pending` 同规：**最后一条 op wins**（同波重复 start 合法，
    重试轮覆盖），坏行/半行/非本相位行跳过。games 载荷按 journal 原样存的字符串
    键回读，转回 int。
    """
    games_by_wave: dict[int, dict[int, int]] = {}
    op_by_wave: dict[int, str] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("phase") != WAVE_PHASE:
            continue
        key = _parse_round_key(str(rec.get("round", "")))
        if key is None or key[0] != int(it):
            continue
        wave_idx = key[1]
        op_by_wave[wave_idx] = str(rec.get("op", ""))
        raw = rec.get("games")
        if isinstance(raw, dict):
            games: dict[int, int] = {}
            for k, v in raw.items():
                try:
                    games[int(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            games_by_wave[wave_idx] = games
    out: dict[int, WaveRecord] = {}
    for wave_idx, op in op_by_wave.items():
        out[wave_idx] = WaveRecord(
            wave_idx=wave_idx,
            games=games_by_wave.get(wave_idx, {}),
            finished=op == "finish",
        )
    return out


def wave_pairs(
    rotate_seed: int,
    it: int,
    games_by_stage: Mapping[int, int],
    wave_idx: int,
) -> list[tuple[int, int]]:
    """波计划 → `(stage, seed)` 派发序列（按 stage 升序遍历，逐关独立抽签）。

    stage 顺序固定（升序）而不是沿用派发顺序：抽签本身按关独立 ⇒ 顺序不影响任何
    一局的种子，排序只是让「同一计划的 pair 序列」也逐字节可比（日志/对账友好）。
    """
    pairs: list[tuple[int, int]] = []
    for stage in sorted(int(s) for s in games_by_stage):
        n = int(games_by_stage[stage])
        pairs.extend((stage, sd) for sd in wave_seeds(rotate_seed, it, stage, wave_idx, n))
    return pairs


# ────────────────────────── 计划里的动态采集块（离线腿，2026-09-22）──────────────
#
# 问题：`target_transitions` 的**采集量**按计划反解（per_stage = ceil(target/关数)，
# 初波 G0 = ceil(per_stage/est)），本地集群靠 `_volume_topup` 逐关补波逼近达标线。
# 而全离线/半离线腿（kind=run）的语料来自 `biz/plan.pairs_for` → 老的 `build_pairs`
# **完全不认 target_transitions**（只认 seeds_per_stage / seed_rotate）⇒ 云机采多少局
# 是「课程里那个 seed_rotate 数字」决定的，与本地集群的采集量无关：轮转数配小了
# （seed_rotate < G0）就**少采**，而云机没有补波机制（一轮一个 job，PPO 在 job 里跑完），
# 少采的部分直接变成「这一轮训练的样本少于目标」——静默的配额缺口。
#
# 修法：把「初波对集」这个构造抽成**两侧共用的纯函数**（`initial_wave_pairs`），计划带上
# 反解所需的全部数字（`volume_block`），节点用同一份代码重放 ⇒ 云机的每轮语料与本地
# 集群的**初波**逐位相同（同 tag 0 种子流、同 G0），训练侧再用既有的 per_stage_quota
# 截到与本地完全相同的样本量。
#
# ⚠ 残留（计划自带、节点无 hub 可查，所以只能这样）：云机**只跑初波**，补波是本地集群
#   才有的机制。est 偏小（少采）时本地会补到达标，云机会短一点点；计划因此把 est **钉死**
#   在导出那一刻的估计值上（见 `loop_export._volume_plan_block`），而不是让节点现算。

#: `plan["volume"]` 的字段集（hub 组装 / 节点重放共用的形状；少一个字段就拒收）。
#: ⚠ 这是**老五字段**（今天的重放路径只认它）；分关分配的扩展字段见
#: `volume_alloc.validate_volume_block_ext`（additive：缺席即老行为）。
VOLUME_BLOCK_FIELDS: tuple[str, ...] = (
    "target_transitions",
    "est_samples_per_game",
    "stages",
    "games_per_stage",
    "per_stage_quota",
)


def volume_block(
    args: Any,
    *,
    est_samples_per_game: int = 0,
    ests_by_stage: Mapping[int, int] | None = None,
    est_hi_factor: float | None = None,
    topup: Mapping[str, Any] | None = None,
    stages_override: Sequence[int] | None = None,
) -> dict | None:
    """args → 计划里的动态采集块；**None = 未开动态采集**（老口径 `build_pairs`）。

    `target_transitions ≤ 0` ⇒ None（老课程一个函数都不调，逐字节不变）。开了就必须
    齐全：缺显式 `--stages`（分关配额的分母）或 est ≤ 0 一律**响亮报错**——静默降级成
    老口径会让云机的采集量与目标脱钩，正是这个块存在要防的事。

    `est_samples_per_game` 由调用方给（hub 侧给的是**当前盘上历史派生**的 trailing 估计）
    ——与 `trainer/loop_volume._volume_est_samples` 同口径，两侧反解出同一个 G0。
    **`0` = 无估计**（首轮无历史）**合法**：`games_per_stage = 1`（不猜），补差批数上界换成
    `DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN`（plan/rollout-estfree-worksteal §4.1）。

    `ests_by_stage`（2026-10-07，plan/rollout-stage-balance §4.3）非空 ⇒ 追加**分关**字段：
    `est_s_by_stage` / `games_per_stage_by_stage`（按 `est_hi = ceil(est×factor)` 反解）/
    `est_hi_factor` / `topup{...}`。老五字段**照旧写**（老重放路径与在飞计划兼容读）。
    缺关的 est 回退全局 `est`（不静默丢关：`validate_volume_block_ext` 会要求关集一致）。
    """
    target = int(getattr(args, "target_transitions", 0) or 0)
    if target <= 0:
        return None
    raw = str(getattr(args, "stages", "") or "").strip()
    if not raw:
        raise ValueError(
            "动态采集（target_transitions > 0）需要显式 --stages 才能分关配额"
            "（缺它就只能静默按固定关集猜，采集量必然与目标不符）"
        )
    stages = parse_stages_arg(raw)
    # 逐轮采样子集（2026-10-27，plan/it-stage-ratation §3.2）：窗口**由调用方算好传进来**
    # （`worker.stage_rotation.window_from_args`，三条腿共用一份），本函数只负责「按这个
    # 关集反解」。⚠ 必须校验它是 `--stages` 的子集：rotation 配置里打错一个关号，静默
    # 当成真关集 = 采一关不存在的关（局局零样本、还把配额吃光）。
    if stages_override is not None:
        window = sorted({int(s) for s in stages_override})
        if not window:
            raise ValueError("stages_override（本轮窗口）不能为空")
        unknown = [s for s in window if s not in set(stages)]
        if unknown:
            raise ValueError(f"stages_override 里有不在 --stages 的关号：{unknown}（--stages={stages}）")
        stages = window
    # est 已从课程/CLI 面退场（2026-10-12）：**不再**读 `args.est_samples_per_game` 兜底；
    # 调用方给的 trailing 估计就是唯一输入，0 = 无估计（不猜档）。
    est = int(est_samples_per_game or 0)
    if est < 0:
        raise ValueError(f"动态采集的 est_samples_per_game 不得为负（0 = 无估计），收到 {est}")
    block: dict = {
        "target_transitions": target,
        "est_samples_per_game": est,
        "stages": [int(s) for s in stages],
        "games_per_stage": int(initial_games(target, len(stages), est)),
        "per_stage_quota": int(target_per_stage(target, len(stages))),
    }
    if ests_by_stage:
        from worker.volume_alloc import (
            DEFAULT_EST_HI_FACTOR,
            DEFAULT_LAST_BATCH_LO_FACTOR,
            DEFAULT_TOPUP_MAX_BATCHES,
            DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN,
            alloc_games_by_stage,
            est_hi,
        )

        factor = float(DEFAULT_EST_HI_FACTOR if est_hi_factor is None else est_hi_factor)
        # 缺关的 est 回退全局 est（可能是 0 = 无估计 ⇒ 该关 1 局，见 alloc_games_by_stage）。
        est_s = {int(s): int(ests_by_stage.get(int(s), 0) or est) for s in stages}
        block["est_s_by_stage"] = est_s
        block["est_hi_factor"] = factor
        block["games_per_stage_by_stage"] = {
            int(s): int(n)
            for s, n in alloc_games_by_stage(
                [int(s) for s in stages],
                target,
                ests_hi={s: est_hi(est_s[s], factor=factor) for s in est_s},
            ).items()
        }
        block["topup"] = {
            "enabled": True,
            # 无估计 ⇒ 初批只有每关 1 局，缺口全靠满铺批填 ⇒ 上界换成防跑飞的绝对值。
            "max_batches": int(
                DEFAULT_TOPUP_MAX_BATCHES if est > 0 else DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN
            ),
            "last_lo_factor": float(DEFAULT_LAST_BATCH_LO_FACTOR),
            "max_games_per_stage": int(getattr(args, "max_games_per_stage", 0) or 0),
            **dict(topup or {}),
        }
    return block


# ------------------------------------------------------------------ 逐轮窗口
#
# 窗口数学（`window_for` 等）**住在这一层**（2026-10-28）：`worker.plan`(L4) 要按轮重解窗口，
# 而 `worker.stage_rotation` 是本层之上的 L4 门面 ⇒ 核心下沉到这里，L4→L4 的上向边不存在。
# `worker.stage_rotation` 继续以同名转发（`X as X`），读者一行不改；窗口仍只有一个定义。

#: 窗口排列的流标签（与 `biz/course.py` 的 `--rotate-stages` 同一个常量：同一个
#: `(rotate_seed, epoch_idx)` 在两处抽出同一个排列，便于把老旋钮与新模式对账）。
EPOCH_TAG = 0xA11CE

#: 支持的模式（`""` = 关闭；关闭时调用方拿到**全集**，无需分支）。
ROTATION_MODES = ("even", "pinned")


def _window_positions(rotate_seed: int, epoch_idx: int, n: int) -> list[int]:
    """`0..n-1` 的一个确定性排列（epoch 键控：同一 epoch 内的所有窗口共用一个排列）。"""
    rng = np.random.default_rng([int(rotate_seed), EPOCH_TAG, int(epoch_idx)])
    return [int(i) for i in rng.permutation(int(n))]


def _rotate_window(
    stages: Sequence[int], *, it: int, rotate_seed: int, k: int
) -> list[int]:
    """在 `stages` 上取第 `it` 轮的窗口（`ceil(n/k)` 轮铺满一遍，窗口间不重叠）。"""
    n = len(stages)
    if n <= 0:
        return []
    if n == 1:
        return [int(stages[0])]
    per_epoch = -(-n // k)
    pos = (int(it) - 1) % per_epoch
    epoch_idx = (int(it) - 1) // per_epoch
    perm = _window_positions(rotate_seed, epoch_idx, n)
    return [int(stages[i]) for i in perm[pos * k : (pos + 1) * k]]


def window_for(
    *,
    mode: str,
    it: int,
    all_stages: Sequence[int],
    rotate_seed: int,
    per_it: int = 0,
    pinned: Sequence[int] = (),
) -> list[int]:
    """第 `it` 轮该采哪些关（**排序**；确定性；`mode` 空 ⇒ 全集）。

    * `even`：每轮 `per_it` 关，`ceil(n/per_it)` 轮覆盖一遍（`per_it >= 1` 必须显式给）；
    * `pinned`：`pinned` 每轮恒在，余额 `per_it` 关从「其余关」里轮换（`per_it = 0` 合法
      = 每轮只采 pinned）；
    * `mode` 为空 ⇒ 返回全集（关闭窗口）。

    失败一律 `ValueError`（调用方转 SystemExit / ProtocolError —— 静默取一个窗口 = 采集量
    对不上目标，最难发现那种）。
    """
    stages = sorted({int(s) for s in all_stages})
    if not stages:
        raise ValueError("stage_rotation 需要至少一个 stage（all_stages 为空）")
    if int(it) <= 0:
        raise ValueError(f"stage_rotation 的 it 必须 >= 1（收到 {it}）")
    mode = str(mode or "").strip()
    if not mode:
        return stages
    if mode not in ROTATION_MODES:
        raise ValueError(
            f"未知的 rotation_mode={mode!r}（支持 {' / '.join(ROTATION_MODES)}；空 = 关闭）"
        )

    stage_set = set(stages)
    pin = sorted({int(s) for s in pinned})
    unknown = [s for s in pin if s not in stage_set]
    if unknown:
        raise ValueError(f"rotation_pinned 里有不在 --stages 的关号：{unknown}")

    if mode == "even":
        if int(per_it) < 1:
            raise ValueError("rotation_mode=even 需要 rotation_per_it >= 1")
        return sorted(
            _rotate_window(stages, it=int(it), rotate_seed=int(rotate_seed), k=int(per_it))
        )

    # pinned：pinned 恒在 + 余额轮换（per_it=0 ⇒ 只采 pinned，合法）
    rest = [s for s in stages if s not in set(pin)]
    k = int(per_it or 0)
    if k <= 0:
        return pin
    return sorted(pin + _rotate_window(rest, it=int(it), rotate_seed=int(rotate_seed), k=k))


def window_from_recipe(
    recipe: Any, it: int, all_stages: Sequence[int], *, rotate_seed: int
) -> list[int]:
    """计划里的配方 → 第 `it` 轮的窗口（与 `window_from_args` 同一个 `window_for`）。

    配方缺席/`mode` 为空 ⇒ 全集（关闭窗口；调用方无需分支）。配置非法（未知 mode、
    `pinned` 不在关集）照旧 `ValueError`——计划层的调用方转 `ProtocolError`（拒收而不是
    跑到半途）。
    """
    if not isinstance(recipe, Mapping):
        return sorted({int(s) for s in all_stages})
    mode = str(recipe.get("mode") or "").strip()
    if not mode:
        return sorted({int(s) for s in all_stages})
    return window_for(
        mode=mode,
        it=int(it),
        all_stages=all_stages,
        rotate_seed=int(rotate_seed),
        per_it=int(recipe.get("per_it") or 0),
        pinned=[int(s) for s in (recipe.get("pinned") or ())],
    )


def event_from_recipe(
    recipe: Any,
    window: Sequence[int],
    *,
    all_stages: Sequence[int],
    override: str = "",
) -> dict | None:
    """**云机轮**的 `rotation` 读数（形状与 `stage_rotation.event_from_args` 一字不差）。

    `override` 非空 = 本轮的窗口不是配方解出来的，而是笔记本开关强制的：账本里记
    `mode = override`（例 `off` = 笔记本把轮换降级成全关采样）+ `override` 一格
    ——语料分布变了就必须留痕（§15.5 的精神：不许静默漂移）；没有窗口可记
    （配方缺席且没有 override）⇒ None（调用方不写键，老账本行逐字节不变）。
    """
    mode = str(override or "").strip() or str(
        (recipe or {}).get("mode") if isinstance(recipe, Mapping) else ""
    ).strip()
    if not mode:
        return None
    if override:
        per_it, pinned = 0, []
    else:
        per_it = int((recipe or {}).get("per_it") or 0)
        pinned = [int(s) for s in ((recipe or {}).get("pinned") or ())]
    ev: dict = {
        "mode": mode,
        "per_it": per_it,
        "pinned": pinned,
        "all": len(sorted({int(s) for s in all_stages})),
        "window": [int(s) for s in window],
    }
    if override:
        ev["override"] = str(override)
    return ev


def volume_block_for_window(block: Mapping[str, Any], *, window: Sequence[int]) -> dict:
    """计划里的 `volume` 块 → **指定窗口**那一轮的块（逐关局数按同一分配器重解）。

    为什么需要它（2026-10-28，云机轮换 · plan/it-stage-ratation §9.5）：`plan["volume"]` 是
    **一份**块（钉着导出那一轮的窗口），而窗口是 `it` 的函数 ⇒ 多轮段里每轮都得按**当轮
    窗口**重解一遍，否则第 2 轮起还在用第 1 轮的关集（`pairs_fp` 逐轮重放会当场抓住）。

    分关局数的唯一来源 = `alloc_games_by_stage(window, target, est_hi(est_s))`（与
    `volume_block` 同一个分配器）：`est_s` 取块里的全关 est 表（`est_s_all`，导出那一刻从
    盘上历史钉下来）——**缺关响亮报错**，静默回退全局 est 会让「这一关铺几局」与本机链不同源。

    返回值：`stages` / `games_per_stage` / `per_stage_quota` / `est_s_by_stage` /
    `games_per_stage_by_stage` 按新窗口重解，其余字段（`target_transitions` /
    `est_samples_per_game` / `est_hi_factor` / `topup` / `est_s_all`）原样带过；
    `widen_width` 一律**弹掉**（加宽是运行时的事：`remote/plan_run` 每轮按本机宽度重铺）。
    """
    stages = sorted({int(s) for s in window})
    if not stages:
        raise ValueError("volume_block_for_window: 窗口不能为空")
    target = int(block["target_transitions"])
    est = int(block.get("est_samples_per_game", 0) or 0)
    raw_all = block.get("est_s_all")
    if not isinstance(raw_all, Mapping):
        raise ValueError(
            "volume 块缺 est_s_all（开了轮换的计划必须钉**全关** est 表——派生非导出轮的窗口要用它）"
        )
    ests_all: dict[int, int] = {}
    for k, v in raw_all.items():
        if isinstance(v, bool) or not isinstance(v, int) or int(v) < 0:
            raise ValueError(f"volume.est_s_all[{k!r}] 必须是非负整数（0 = 无估计），收到 {v!r}")
        ests_all[int(k)] = int(v)
    missing = [s for s in stages if s not in ests_all]
    if missing:
        raise ValueError(f"volume.est_s_all 缺关: {missing}（窗口里的每一关都要有估计）")
    from worker.volume_alloc import (
        DEFAULT_EST_HI_FACTOR,
        alloc_games_by_stage,
        est_hi,
    )

    factor = float(block.get("est_hi_factor") or DEFAULT_EST_HI_FACTOR)
    # `or est` 与 `volume_block` 逐字同规（est_s_all 里的 0 = 该关无估计 ⇒ 回退全局 est）。
    est_s = {s: int(ests_all[s] or est) for s in stages}
    out = dict(block)
    out.update(
        {
            "stages": stages,
            "games_per_stage": int(initial_games(target, len(stages), est)),
            "per_stage_quota": int(target_per_stage(target, len(stages))),
            "est_s_by_stage": est_s,
            "est_hi_factor": factor,
            "games_per_stage_by_stage": {
                int(s): int(n)
                for s, n in alloc_games_by_stage(
                    stages,
                    target,
                    ests_hi={s: est_hi(est_s[s], factor=factor) for s in est_s},
                ).items()
            },
        }
    )
    out.pop("widen_width", None)
    return out


def initial_wave_pairs_by_stage(
    rotate_seed: int, it: int, *, games_by_stage: Mapping[int, int]
) -> list[tuple[int, int]]:
    """分关初波：每关各自的局数（`G_s`），按 stage 升序、逐关独立抽签（同 0x5EED 流）。

    与 `initial_wave_pairs`（单一 `games_per_stage`）**同一个 `wave_pairs`**：所以「本地
    连续流的前缀」这条不变量对每一关分别成立，节点补差从 `G_s` 续抽即接得上。
    """
    return wave_pairs(
        int(rotate_seed),
        int(it),
        {int(s): int(n) for s, n in games_by_stage.items()},
        0,
    )


def validate_volume_block(block: object) -> dict:
    """计划里的 `volume` 块形状校验（畸形 ⇒ ValueError；`biz/plan.validate_plan` 转协议错）。

    老五字段照旧；带分关扩展字段（`games_per_stage_by_stage` 等）时，同源校验交给
    `volume_alloc.validate_volume_block_ext`（**一份**公式，不在这里抄第二份）。
    """
    if not isinstance(block, dict):
        raise ValueError(f"volume 必须是对象，收到 {type(block).__name__}")
    missing = [k for k in VOLUME_BLOCK_FIELDS if k not in block]
    if missing:
        raise ValueError(f"volume 缺字段: {missing}")
    for k in ("target_transitions", "games_per_stage", "per_stage_quota"):
        v = block[k]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"volume.{k} 必须是整数，收到 {v!r}")
        if v <= 0:
            raise ValueError(f"volume.{k} 必须 ≥ 1，收到 {v!r}")
    # est：0 合法 = **无估计**（首轮无历史）⇒ `games_per_stage == 1`（不猜档）；负数是畸形。
    raw_est = block["est_samples_per_game"]
    if isinstance(raw_est, bool) or not isinstance(raw_est, int) or raw_est < 0:
        raise ValueError(
            f"volume.est_samples_per_game 必须是非负整数（0 = 无估计），收到 {raw_est!r}"
        )
    stages_raw = block["stages"]
    if not isinstance(stages_raw, list) or not stages_raw:
        raise ValueError(f"volume.stages 必须是非空数组，收到 {stages_raw!r}")
    stages: list[int] = []
    for s in stages_raw:
        if isinstance(s, bool) or not isinstance(s, int):
            raise ValueError(f"volume.stages 必须是整数数组，收到 {s!r}")
        stages.append(int(s))
    want_quota = target_per_stage(int(block["target_transitions"]), len(stages))
    if want_quota != int(block["per_stage_quota"]):
        raise ValueError(
            f"volume.per_stage_quota={block['per_stage_quota']} 与"
            f"ceil(target/关数)={want_quota} 不符（分关达标线两侧必须同源）"
        )
    want_g0 = initial_games(
        int(block["target_transitions"]), len(stages), int(block["est_samples_per_game"])
    )
    if want_g0 != int(block["games_per_stage"]):
        raise ValueError(
            f"volume.games_per_stage={block['games_per_stage']} 与"
            f" ceil(达标线/est)={want_g0} 不符（局数反解两侧必须同源；est=0 ⇒ 1）"
        )
    if "games_per_stage_by_stage" in block or "est_s_by_stage" in block or "topup" in block:
        from worker.volume_alloc import validate_volume_block_ext

        out = validate_volume_block_ext({**block, "stages": stages})
        return out
    out = dict(block)
    out["stages"] = stages
    return out


def initial_wave_pairs(
    rotate_seed: int, it: int, *, stages: Sequence[int], games_per_stage: int
) -> list[tuple[int, int]]:
    """本轮的**初波前缀**对集：每关 `games_per_stage`（G0）局，按 stage 升序。

    本地集群的预排表（`TrainingLoop._iteration_pairs` 的 volume 分支）与节点重放
    （`biz/plan.pairs_for`）**共用本函数**——同一 (rotate_seed, it, 关, G0) ⇒ 同一批种子，
    这是「云机的语料与本地集群 rollout 一致」的实现基础。

    ★ 为什么它就是「本地集群实际采的前 G0 局」（2026-09-22 核过）：本轮本地走的是
    **连续配额**（`biz/volume_quota`，VOLUME_RULE_V2），它的种子流键是
    `[rotate_seed, 0x5EED, it, stage, 0]`，而本函数的 wave 0 流键是
    `[rotate_seed, 0x5EED, it, stage, 0]` —— **同一个键**（见 `wave_seed_stream`：
    `wave_idx <= 0` 取 tag 0x5EED，第 5 键就是 wave_idx 本身⇒ 0）。所以本函数输出的
    正是各关连续流上 **第 0..G0 个** seed ⇒ 云机采的就是本地集群同一轮的前缀批
    （`tests/worker/test_volume_plan_block.py::test_initial_pairs_are_continuous_prefix` 钉住）。
    """
    return wave_pairs(
        int(rotate_seed),
        int(it),
        {int(s): int(games_per_stage) for s in stages},
        0,
    )


def volume_pairs_from_args(
    args: Any,
    it: int,
    rotate_seed: int,
    *,
    est_samples_per_game: int = 0,
    ests_by_stage: Mapping[int, int] | None = None,
    est_hi_factor: float | None = None,
    stages_override: Sequence[int] | None = None,
) -> list[tuple[int, int]] | None:
    """args → 本轮初波对集（`None` = 未开动态采集）；计划发布期自检用它对照重放结果。

    `ests_by_stage` / `est_hi_factor` 由调用方（`check_plan_against_args`）从**计划块**注入：
    分关 G 是「est 决定的局数」这类运行参数，args 侧无法独立复算出来 ⇒ 自检守的是
    stages/target/模板恒等 + 块自身的同源（后者在 `validate_volume_block` 里已拦一次）。
    """
    block = volume_block(
        args,
        est_samples_per_game=est_samples_per_game,
        ests_by_stage=ests_by_stage,
        est_hi_factor=est_hi_factor,
        stages_override=stages_override,
    )
    if block is None:
        return None
    per_stage = block.get("games_per_stage_by_stage")
    if isinstance(per_stage, dict) and per_stage:
        return initial_wave_pairs_by_stage(rotate_seed, it, games_by_stage=per_stage)
    return initial_wave_pairs(
        rotate_seed, it, stages=block["stages"], games_per_stage=block["games_per_stage"]
    )

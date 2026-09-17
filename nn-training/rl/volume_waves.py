"""volume_waves.py —— 按样本量动态采集的纯逻辑（plan/dynamic-rollout-volume.plan.md §2，P0）。

课程用 `target_transitions`（/轮）代替固定局数做采集配额：初波按目标量反解每关局数，
结算后按**已结算 transitions** 逐关补波，直到达标或触硬顶。

本模块只有纯函数与常量（无 IO、无 torch）：配额数学、波次种子流、终止谓词、补波计划。
shard 账本读取在 `rl/resume.py`（settled_stage_totals / trailing_samples_per_game），
loop 接线在 `rl/loop_core.py::_volume_topup`。

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

import numpy as np

#: 规则版本常量——进 `corpus_identity_fp`（采样规则变更 = 新实验，§15.5）。
VOLUME_RULE_V1 = 1

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


def _ceil_div(a: int, b: int) -> int:
    """正整数向上取整除法（b>0）。"""
    return -(-a // b)


def parse_stages_arg(raw: str) -> list[int]:
    """`--stages` 字符串 → 关号列表（**共享解析器**）。

    抽出来是为了消除「同一份 `--stages` 被两处各解析一遍」的漂移风险：
      · `loop_core._volume_stages()`（采集侧）：解析失败**响亮 SystemExit**；
      · `loop_steps._per_stage_quota()`（训练侧）：解析失败**静默返 0**（= 全收 = 老行为）。
    两者对同一输入的处理策略不同是**有意的**（采集侧必须响亮，训练侧缺省即老行为），
    但「什么算可解析」必须是同一个判断——否则会出现「采集说合法、训练说非法」的裂缝。

    ⚠ 空串 / 不可解析 ⇒ `ValueError`（**不返回空列表**）：调用方据此区分
    「没给」与「给了坏值」，空列表会让 `target_per_stage` 的 n_stages≤0 分支报错在更远处。
    """
    from rl.course import parse_range

    text = str(raw or "").strip()
    if not text:
        raise ValueError("--stages 为空")
    return parse_range(text)


def target_per_stage(target_transitions: int, n_stages: int) -> int:
    """分关达标线 = ceil(target / n_stages)（n_stages ≤ 0 响亮报错）。

    P2-6（2026-09-15）：`target_transitions ≤ 0` 也响亮报错。原先 `max(0, target)`
    把 0 静默变成「达标线 0」⇒ 每关第一波就 `quota_met` 立即停，采集量掉到 G0
    而不报错——是那种最难发现的静默失效。开动态采集的前提就是 target > 0
    （`loop_core._volume_active` 同口径），所以这里报错不会误伤老课程：
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
    与 `target_transitions` 同单位。≤ 0 响亮报错——配额反解没有估计值就是静默乱采
    （配置校验在 CourseConfig 层已经拦一次，这里再拦是为了纯函数自洽）。
    **est 不被钳到任何下限**（2026-09-15 回退 P2-b；理由见文件头那段长注释）。
    """
    if est_samples_per_game <= 0:
        raise ValueError(
            f"initial_games 需要 est_samples_per_game ≥ 1，得到 {est_samples_per_game}"
        )
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
class TopUpPlan:
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

    @property
    def games_total(self) -> int:
        return sum(self.games_by_stage.values())


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


#: commit journal 里本模块使用的相位名（与 loop_core 的 `_volume_topup` 同一常量）。
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

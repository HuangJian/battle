"""RLConfig —— 训练启动参数校验层（P1-3，2026-09-02；pydantic 版）。

背景：`args`（argparse.Namespace）穿透 15 个模块，`getattr(args, "x", default)`
的静默默认让**拼错/非法组合在训练中途才暴露**。全量改造成并替换所有下游签名的
风险/收益不成比例（三后端仍在演进）。

本模块折中：**保留 Namespace 穿透，但把关键参数收成 RLConfig 并在启动期校验**。

2026-09-02（库复用）：手写校验迁移到 **pydantic**（成熟库）——字段类型自动
强制（str→int/float）、范围/互斥用 field_validator / model_validator 声明式表达，
`ValidationError` 一次聚合全部错误。`validate()` 保留为兼容接口（返回错误列表，
供测试与旧调用方）；`validate_args` 是 run_rl 启动期的 fail fast 入口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from rl.reward_library import OUTCOMES

#: 课程配置目录（nn-training/curricula/*.jsonc）
CURRICULA_DIR = Path(__file__).resolve().parent.parent / "curricula"
#: stageJson 查询串上限（评审 LC §4.4）：13×13 grid ~700 字节，4KB 留足余量
STAGE_JSON_MAX_BYTES = 4096
#: 自定义关（配置内 grid）起始 ID：第 i 个 → 2000+i（plan §5.2）
CUSTOM_STAGE_BASE = 2000


class RLConfig(BaseModel):
    """训练启动关键参数（校验视图；不承载全部 argparse 字段）。"""

    mode: str = "per-tick"
    iters: int = 0  # <=0 = 无限
    stream: int = 1
    double_buffer: int = 0
    precollect_games: int = 0
    precollect_samples: int = 0
    workers: int = 8
    local_slots: int = 0
    mb: int = 512
    epochs: int = 4
    lr: float = 3e-4
    seed: int = 7
    keep_iters: int = 3
    stop_loss_at: int = 0
    stop_loss_delta: float = 0.0
    adv_norm: str = "auto"
    eval_seeds: int = 10
    eval_at: str = ""
    reward: str = ""

    @field_validator("workers")
    @classmethod
    def _workers_ge1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("workers 非法（≥1）")
        return v

    @field_validator("mb", "epochs", "eval_seeds")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("该参数非法（≥1）")
        return v

    @field_validator(
        "local_slots", "keep_iters", "stop_loss_at", "precollect_games", "precollect_samples"
    )
    @classmethod
    def _nonneg_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("该参数非法（≥0）")
        return v

    @field_validator("lr")
    @classmethod
    def _lr_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("lr 非法（>0）")
        return v

    @field_validator("stream", "double_buffer")
    @classmethod
    def _flag01(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("该参数非法（0/1）")
        return v

    @field_validator("adv_norm")
    @classmethod
    def _adv_norm_valid(cls, v: str) -> str:
        if v not in ("auto", "global", "wave", "none"):
            raise ValueError("adv_norm 非法（auto/global/wave/none）")
        return v

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in ("per-tick", "intent", "goal"):
            raise ValueError("mode 非法（per-tick/intent/goal）")
        return v

    @field_validator("reward")
    @classmethod
    def _reward_valid(cls, v: str) -> str:
        # P1-12：'' / 'v7' / 'toy:<arm>'——防拼错静默走默认
        if v and v != "v7" and not v.startswith("toy:"):
            raise ValueError("reward 非法（'' 按 stage 解析 / 'v7' / 'toy:<arm>'）")
        if v.startswith("toy:") and len(v) <= len("toy:"):
            raise ValueError("reward 非法（toy:<arm> 需要具体 arm）")
        return v

    @model_validator(mode="after")
    def _check_mutual_exclusion(self):
        if self.precollect_games > 0 and self.precollect_samples > 0:
            raise ValueError(
                "precollect_games 与 precollect_samples 互斥——双缓冲只能选一种"
                "提前采样的度量口径（游戏数 或 样本量）"
            )
        return self

    def collect_errors(self) -> list[str]:
        """兼容接口：返回配置错误列表（空 = 合法）。

        pydantic 语义下非法配置在**构造时**即抛 ValidationError；本方法供
        需要"构造后收集错误"的调用方（重跑全部校验器并捕获）。
        """
        try:
            self.model_validate(self.model_dump())
        except ValidationError as e:
            return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        return []


def _errors_of(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors()]


def validate_args(args) -> None:
    """从 argparse.Namespace 构建 RLConfig 并校验；非法即 SystemExit（启动期 fail fast）。

    P1-3：`--precollect-games 5 --precollect-samples 1000` 这类互斥组合此前要到
    训练中途才暴露，现在启动即报（pydantic 一次聚合全部错误）。
    """
    from rl.log import log

    try:
        RLConfig(
            mode=getattr(args, "mode", "per-tick"),
            iters=getattr(args, "iters", 0),
            stream=int(getattr(args, "stream", 1)),
            double_buffer=int(getattr(args, "double_buffer", 0) or 0),
            precollect_games=int(getattr(args, "precollect_games", 0) or 0),
            precollect_samples=int(getattr(args, "precollect_samples", 0) or 0),
            workers=getattr(args, "workers", 8),
            local_slots=int(getattr(args, "local_slots", 0) or 0),
            mb=getattr(args, "mb", 512),
            epochs=getattr(args, "epochs", 4),
            lr=float(getattr(args, "lr", 3e-4)),
            seed=getattr(args, "seed", 7),
            keep_iters=getattr(args, "keep_iters", 3),
            stop_loss_at=getattr(args, "stop_loss_at", 0),
            stop_loss_delta=float(getattr(args, "stop_loss_delta", 0.0)),
            adv_norm=getattr(args, "adv_norm", "auto"),
            eval_seeds=getattr(args, "eval_seeds", 10),
            eval_at=getattr(args, "eval_at", ""),
            reward=getattr(args, "reward", ""),
        )
    except ValidationError as e:
        for err in _errors_of(e):
            log(f"[config] ERROR: {err}")
        raise SystemExit(
            "[run_rl] 启动参数非法（见上）——修复后重试；"
            "这些错误此前要等训练中途才暴露（P1-3 启动期校验）"
        ) from None
    # ===== 远程模式（--ppo remote，plan §5）：内部强制 stream=0 + skip 模型构建；
    # 与 --stream 1 / --double-buffer **显式互斥**——config 默认值（rl.<mode>.stream
    # 对本地模式正确）静默降 0，用户显式传 --stream 1 / --double-buffer 1 才报错
    # （run_rl.main 用 parse_args([]) 基线把显式性存到 args._explicit_*）。=====
    if getattr(args, "ppo", "local") == "remote":
        if getattr(args, "_explicit_stream", False) and int(getattr(args, "stream", 0) or 0):
            raise SystemExit(
                "[run_rl] --ppo remote 与显式 --stream 1 互斥（远程 = 每迭代结算一次 "
                "PPO，wave 级 stream 只留本地模式）——删掉 --stream 1"
            )
        if getattr(args, "_explicit_double_buffer", False) and int(
            getattr(args, "double_buffer", 0) or 0
        ):
            raise SystemExit(
                "[run_rl] --ppo remote 与显式 --double-buffer 互斥（远程预采由 "
                "--remote-precollect 控制，Q10 默认 0）——删掉 --double-buffer"
            )
        if getattr(args, "mode", "per-tick") != "per-tick":
            raise SystemExit(
                "[run_rl] --ppo remote 仅支持 per-tick 课程（v1 红线）——"
                f"收到 mode={getattr(args, 'mode', 'per-tick')}"
            )
        # 静默降级（内部强制）：config 默认 stream/double-buffer 不适用于远程
        args.stream = 0
        args.double_buffer = 0
        log("[run_rl] remote mode: stream/double-buffer forced to 0 (config defaults suppressed)")
    # ===== BC-anchored kickstart（§363）：per-tick 缰绳双闸 =====
    # 缰绳必须 it1 就勒住（smash 轮）——warmup_iters!=0 会让首轮系数归零（见
    # run_rl.update_kwargs），属配置自相矛盾，启动期响亮拒绝。
    if bool(getattr(args, "kickstart_ref", False)):
        if getattr(args, "mode", "per-tick") != "per-tick":
            raise SystemExit(
                "[run_rl] kickstart_ref 只支持 per-tick（intent/goal 自带 out 系 ref，"
                "双锚定语义冲突）——关掉课程 kickstart_ref"
            )
        if int(getattr(args, "warmup_iters", 1) or 0) != 0:
            raise SystemExit(
                "[run_rl] kickstart_ref 要求 warmup_iters=0（缰绳 it1 必须生效；"
                f"当前 {getattr(args, 'warmup_iters', 1)} 会让首轮系数归零）"
            )


# ================================================================== 课程配置
#
# `--course <name>` —— 课程 = 启动参数 + 关卡布局 + 奖励公式的单一事实来源
# （plan/rl-training-config.md）。合并优先级：**课程配置 > rl-config.json >
# argparse 默认**，且**没有**逐参数 CLI 覆盖通道（评审 P1-7：改名 --course 避开
# 既有 --curriculum-stages/--start/--every/--grow 的语义冲突）。
#
# 格式 = JSONC 子集（只 `//` 行注释，见 rl/jsonc.py）；`extra="forbid"` —— 拼错
# 的键直接响亮报错，不静默忽略。


class Spawn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    col: int
    row: int


class SpawnVariant(BaseModel):
    """出生点变体（`spawn_variants` 单元素）：seed 哈希选点后生效的出生点组。

    2026-09-03 p1-onset：固定出生点使 God-AI BC 语料 left 方向仅 1.4% + 模型学到
    位置记忆；变体池让 (stage, seed) 确定性选点，语料/rollout 几何多样化。
    TS 侧解码（src/nn/config-stage.ts::decodeStageGrid）按同一哈希选点——BC 语料
    与 RL rollout 分布自动对齐。"""

    model_config = ConfigDict(extra="forbid")

    player_spawn: Spawn | None = None
    enemy_spawns: list[Spawn] = []


class StageSpec(BaseModel):
    """自定义关卡（`stageData.ts` 13×13 数字瓦格格式，plan §5.1）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    grid: list[list[int]]
    forces: str = "cccccccccccccccccccc"
    count: int | None = None
    player_spawn: Spawn | None = None
    enemy_spawns: list[Spawn] = []
    # 出生点变体池（seed 哈希选点；空 = 顶层 player_spawn/enemy_spawns 固定）
    spawn_variants: list[SpawnVariant] = []

    @field_validator("grid")
    @classmethod
    def _grid_shape(cls, v: list[list[int]]) -> list[list[int]]:
        if not v or len(v) != 13:
            raise ValueError(f"grid 必须 13 行（收到 {len(v)}）")
        for i, row in enumerate(v):
            if len(row) != 13:
                raise ValueError(f"grid 第 {i} 行必须 13 列（收到 {len(row)}）")
        return v

    @field_validator("forces")
    @classmethod
    def _forces_len(cls, v: str) -> str:
        if len(v) != 20:
            raise ValueError(f"forces 必须 20 字符（收到 {len(v)}）")
        bad = set(v) - set("abcd")
        if bad:
            raise ValueError(f"forces 含非法敌种 {sorted(bad)}（a=basic b=fast c=power d=armor）")
        return v


class ParamSchedule(BaseModel):
    """`param_schedule` 单键：`{from, to, until_iter, mode}`（评审 P1-6 单一形状）。"""

    model_config = ConfigDict(extra="forbid")

    fro: float = 0.0
    to: float = 0.0
    until_iter: int
    mode: Literal["linear", "step"] = "linear"

    @model_validator(mode="before")
    @classmethod
    def _alias_from(cls, data: Any) -> Any:
        # "from" 是 Python 关键字 → 配置里仍写 from，载入时改名
        if isinstance(data, dict) and "from" in data:
            data = {**data, "fro": data["from"]}
            data.pop("from", None)
        return data

    @field_validator("until_iter")
    @classmethod
    def _until_ge1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("until_iter 非法（≥1）")
        return v


class RewardBlock(BaseModel):
    """`reward` 块：公式引擎是唯一机制（旧课程公式是它的验收用例）。"""

    model_config = ConfigDict(extra="forbid")

    formula: str = ""
    #: 降级卡回退目标（formula 超限/含白名单外符号时启用，warning 不静默）
    builtin: str = ""
    params: dict[str, float] = {}
    param_schedule: dict[str, ParamSchedule] = {}
    #: outcome 原名 → 终局奖励；未列出 = 0（评审 P1-5）
    terminal: dict[str, float] = {}
    #: toy（terminal 表）| score_reconcile（telescoping 对账到 scale×gatedScore）
    scheme: Literal["toy", "score_reconcile"] = "toy"
    reward_scale: float = 10.0
    #: 扩展层函数（三角/双曲/特殊）opt-in，默认最小攻击面（评审 LC §2.2）
    allow_extended_funcs: bool = False
    #: 终局重分配（DECISIONS §382）：True 时终局额按步均摊，默认 False 历史行为
    terminal_spread: bool = False

    @field_validator("terminal")
    @classmethod
    def _terminal_keys(cls, v: dict[str, float]) -> dict[str, float]:
        bad = set(v) - set(OUTCOMES)
        if bad:
            raise ValueError(
                f"terminal 键非法 {sorted(bad)}（须为 outcome 原名：{list(OUTCOMES)}）"
            )
        return v


class PpoScheduleEntry(BaseModel):
    """`ppo_schedule` 分段表项：按**绝对 iter** 查表（评审 R1-6）。"""

    model_config = ConfigDict(extra="forbid")

    until_iter: int | None = None  # 末段可省略 → 兜底
    lr: float | None = None
    epochs: int | None = None
    mb: int | None = None
    kl_coef: float | None = None
    kl_cap: float | None = None


# ---------------------------------------------------------------------------
# 课程结束门（plan/course-exit-and-shutdown.md §3；DECISIONS §337/§338）
# ---------------------------------------------------------------------------
#
# 顶层可选块 `"gates"`：门从课程注释走进代码。缺席 = 关闭（老课程逐字节不变）。
# 阈值随课程文件进 course_fp（§331-D14）——改阈值 = 新实验版本。
#
# 本模块只管**声明与解析期校验**（M0）；求值器在 `rl/gate_check.py`（M1），
# in-loop 接线在 `rl/loop_guards.py` 第四守卫（M1）。

#: 固定 catalog（§3.3）：每种 kind = 求值器一个函数；未知 kind 响亮报错。
GATE_KINDS: frozenset[str] = frozenset(
    {
        "wins_mastery",  # G1 胜率轨（教师相对）
        "skill_floor",  # G2 技能轨（0 杀占比 / 场均杀 / 被击中）
        "transfer",  # G3 横向准入（跨课探针；首期休眠）
        "plateau",  # G4 边际收益枯竭（前后半均值差 + SE 口径；可分流）
        "budget",  # G5 预算到顶（可分流）
        "duty",  # G13 事故熔断：有效训练占空比过低（2026-09-11 评审新增，§12.4）
        "course_valid",  # G7 课程失效（超时超限 且 斜率>0）
        "retention",  # G8 回退守卫（首期休眠）
        "hack",  # G9 hack 熔断（双向条件，降级 PAUSE）
        "teacher_parity",  # G10 教师触顶
        "dependency",  # G11 依赖就绪（kind 保留，M3 实现）
    }
)

#: 判决字面量（§1）。
GATE_VERDICTS: frozenset[str] = frozenset({"ADVANCE", "REMEDIATE", "ABORT", "PAUSE", "STOP"})
#: G4/G5 的分流声明：verdict 本体单值，求值时填 `route`（§3.4-1）。
GATE_SPLIT = "ADVANCE|REMEDIATE"
GATE_SPLIT_KINDS: frozenset[str] = frozenset({"plateau", "budget"})
#: plateau.metrics 允许的键（= summary 行里带分母的聚合量）。
GATE_PLATEAU_METRICS: frozenset[str] = frozenset(
    {"win_rate", "kills_mean", "phits_mean", "pickup_mean", "timeout_frac"}
)
#: 依赖跨课评估管线的 kind（p10 成稿前只能声明 enabled:false——§3.3 首期休眠）。
GATE_CROSS_COURSE_KINDS: frozenset[str] = frozenset({"transfer", "retention", "dependency"})
#: 分数类参数：值域 [0,1]。
#:
#: 2026-09-10 实现期修正：§3.4-5 原文写"比例类 ∈ [0,1]（rel_teacher 可 >1）"，
#: 但同节 §3.2 的示例自身给出 G2 `max_phits_rel: 1.5`——**相对倍数**（× 教师）本
#: 就允许 >1。故按语义拆两类：`*_frac`/`min_win_rate`/`advance_frac` 是分数（[0,1]），
#: 一切 `*_rel` 是相对倍数（仅要求 ≥0）。计划原文按批注修正，不拦 1.5 这类合法值。
_GATE_FRAC_FIELDS: tuple[str, ...] = (
    "max_zero_kill_frac",
    "advance_frac",
    "max_timeout_frac",
    "min_win_rate",
    "min_train_frac",  # G13 有效训练占空比（事故熔断）
)
#: 相对倍数参数：仅要求 ≥0（可 >1）。
_GATE_REL_FIELDS: tuple[str, ...] = (
    "rel_teacher",
    "min_kills_rel",
    "max_phits_rel",
    "pickup_up_rel",
    "kills_down_rel",
)
#: 百分点（pp）参数：1.0 = 1pp，仅要求 ≥0（2026-09-11 评审：ADVANCE 需 effect size）。
_GATE_PP_FIELDS: tuple[str, ...] = ("min_gain_pp",)


class GateTeacher(BaseModel):
    """本课教师基线（所有相对门的参照系）。

    `corpus` 必填非空：相对线只在**同语料**下成立（§3.4-6）——语料扩池后本块
    必须重测重填。人读版仍写在课程文件头注释。
    """

    model_config = ConfigDict(extra="forbid")

    corpus: str
    games: int
    wins: int = 0
    kills: float = 0.0
    phits: float = 0.0
    zero_kill_frac: float = 0.0
    timeout_frac: float = 0.0


class GateRule(BaseModel):
    """单条门。kind 决定哪些参数有意义；**未知键响亮报错**（extra=forbid）。

    参数按 kind 分组列出（不为每种 kind 造一个子模型：catalog 固定且求值器是
    纯函数，扁平结构让课程文件保持可读、单测好写）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    verdict: str
    enabled: bool = True

    # wins_mastery / teacher_parity
    rel_teacher: float | None = None
    tol_pp: float | None = None
    # wins_mastery 的 effect-size 附加条件（2026-09-11 评审，§12.4）
    min_gain_pp: float | None = None
    require_rising: bool = False
    # duty（G13 事故熔断）
    min_train_frac: float | None = None
    # skill_floor
    max_zero_kill_frac: float | None = None
    min_kills_rel: float | None = None
    max_phits_rel: float | None = None
    # transfer / retention / dependency
    course: str | None = None
    courses: list[str] = []
    min_wins: int | None = None
    min_kills: int | None = None
    every_rounds: int | None = None
    min_win_rate: float | None = None
    # plateau / course_valid / hack
    window_rounds: int | None = None
    window_iters: int | None = None
    rising_rounds: int | None = None
    tol_kills: float | None = None
    metrics: list[str] = []
    advance_if: list[str] = []
    advance_frac: float | None = None
    max_timeout_frac: float | None = None
    pickup_up_rel: float | None = None
    kills_down_rel: float | None = None

    @property
    def is_split(self) -> bool:
        """G4/G5 的分流声明（verdict 本体仍是单值 ADVANCE）。"""
        return self.verdict == GATE_SPLIT

    @property
    def base_verdict(self) -> str:
        """单值判决（分流声明折叠为 ADVANCE，route 由求值器填）。"""
        return "ADVANCE" if self.is_split else self.verdict


def _gate_ratio(name: str, v: float | None, where: str) -> None:
    if v is None:
        return
    if not 0.0 <= float(v) <= 1.0:
        raise ValueError(f"{where}: {name}={v} 越界（比例类值域 [0,1]，§3.4-5）")


class GatesSpec(BaseModel):
    """课程 `gates` 块（§3.2）。解析期强校验 §3.4 全 8 条。"""

    model_config = ConfigDict(extra="forbid")

    #: 确认类门的连续通过次数（迟滞，§4.4 去重键终结重放污染）。
    sustain: int = 3
    teacher: GateTeacher
    #: ADVANCE 放行所需门（只看其中 `enabled` 的门——休眠门不计，§3.4-1）。
    advance_requires: list[str] = []
    #: G12 人工通道（相对 traj 父目录；先落行、后消费，§4.6）。
    override_file: str = "GATE_OVERRIDE.json"
    #: 可选：单轮迭代分钟数估计。仅用于 §3.4-8 预算可行性 WARNING（不断言）。
    est_iter_min: float | None = None
    rotation_rounds: int | None = None
    #: 课程起点（零样本）胜率——`min_gain_pp` 的参照系（§12.4：ADVANCE 需 effect size）。
    #: 开腿前须用同一批评估种子实测；开腿后改值 = course_fp 变 = 新实验。
    baseline_win_rate: float | None = None
    #: ADVANCE 附加条件：累计有效训练（Σ ppo_sec）不足此时长（小时）→ HOLD，
    #: 不下 ADVANCE/STOP 判决（§12.4：数据还不够下结论 ≠ 事故）。
    min_train_hours: float | None = None
    rules: list[GateRule] = []

    @model_validator(mode="after")
    def _check_rules(self) -> GatesSpec:
        if not self.rules:
            raise ValueError("gates: rules 非空（声明了 gates 块就必须有门）")
        if self.sustain < 1:
            raise ValueError(f"gates: sustain={self.sustain} 必须 ≥1（§3.4-5）")
        if self.est_iter_min is not None and self.est_iter_min <= 0:
            raise ValueError(f"gates: est_iter_min={self.est_iter_min} 必须 >0")

        ids: dict[str, GateRule] = {}
        for r in self.rules:
            where = f"gates.rules[{r.id}]"
            if not r.id:
                raise ValueError("gates: rule.id 不得为空")
            if r.id in ids:
                raise ValueError(f"{where}: rule id 重复")
            ids[r.id] = r
            if r.kind not in GATE_KINDS:
                raise ValueError(f"{where}: 未知 kind '{r.kind}'（catalog: {sorted(GATE_KINDS)}）")
            if r.verdict not in GATE_VERDICTS and r.verdict != GATE_SPLIT:
                raise ValueError(
                    f"{where}: 未知 verdict '{r.verdict}'（{sorted(GATE_VERDICTS)} 或 '{GATE_SPLIT}'）"
                )
            if r.is_split and r.kind not in GATE_SPLIT_KINDS:
                raise ValueError(
                    f"{where}: 分流声明 '{GATE_SPLIT}' 只允许 {sorted(GATE_SPLIT_KINDS)}（§3.4-1）"
                )
            for f in _GATE_FRAC_FIELDS:
                _gate_ratio(f, getattr(r, f), where)
            for f in (*_GATE_PP_FIELDS,):
                v = getattr(r, f)
                if v is not None and v < 0:
                    raise ValueError(f"{where}: {f}={v} 不得为负（百分点参数，5.0 = 5pp）")
            for f in (*_GATE_REL_FIELDS, "tol_pp", "tol_kills"):
                v = getattr(r, f)
                if v is not None and v < 0:
                    raise ValueError(f"{where}: {f}={v} 不得为负")
            if (
                not r.enabled
                and r.kind not in GATE_CROSS_COURSE_KINDS
                and r.kind != "teacher_parity"
            ):
                raise ValueError(
                    f"{where}: 只有 {'/'.join(sorted(GATE_CROSS_COURSE_KINDS))}/teacher_parity "
                    "可休眠（§3.3；其余门要么配、要么不写）"
                )

            # window/rounds 单位一律 = eval 轮（§3.4-3）
            if r.window_rounds is not None and r.window_rounds < 2:
                raise ValueError(f"{where}: window_rounds={r.window_rounds} 必须 ≥2（§3.4-5）")
            if r.window_iters is not None and r.window_iters < 2:
                raise ValueError(f"{where}: window_iters={r.window_iters} 必须 ≥2")
            for f in ("rising_rounds", "every_rounds"):
                v = getattr(r, f)
                if v is not None and v < 1:
                    raise ValueError(f"{where}: {f}={v} 必须 ≥1")

            if r.kind == "plateau":
                if not r.metrics:
                    raise ValueError(f"{where}: plateau 必须给 metrics")
                bad = [m for m in r.metrics if m not in GATE_PLATEAU_METRICS]
                if bad:
                    raise ValueError(
                        f"{where}: metrics 含未知键 {bad}（{sorted(GATE_PLATEAU_METRICS)}）"
                    )
                if r.advance_frac is None:
                    raise ValueError(f"{where}: plateau 必须给 advance_frac（§3.3 分流）")
            if r.kind == "budget" and r.advance_frac is None:
                raise ValueError(f"{where}: budget 必须给 advance_frac（§3.3 路由）")
            if r.kind in GATE_CROSS_COURSE_KINDS:
                targets = ([r.course] if r.course else []) + list(r.courses)
                if not any(targets):
                    raise ValueError(f"{where}: {r.kind} 必须给 course 或 courses")
                _resolve_courses(targets, where)

        # ---- §3.4-1 引用完整性 + verdict 相容 ----
        for ref_field in ("advance_requires",):
            for gid in getattr(self, ref_field):
                if gid not in ids:
                    raise ValueError(f"gates.{ref_field}: 引用了不存在的门 '{gid}'")
                if ids[gid].base_verdict != "ADVANCE":
                    raise ValueError(
                        f"gates.{ref_field}: '{gid}' 的 verdict 不是 ADVANCE/分流（§3.4-1）"
                    )
        for r in self.rules:
            for gid in r.advance_if:
                if gid not in ids:
                    raise ValueError(f"gates.rules[{r.id}].advance_if: 未定义的门 '{gid}'")
                if ids[gid].base_verdict != "ADVANCE":
                    raise ValueError(
                        f"gates.rules[{r.id}].advance_if: '{gid}' 不是 ADVANCE/分流（§3.4-1）"
                    )

        # ---- §3.4-7 / §3.4-6 teacher 块 ----
        if not str(self.teacher.corpus or "").strip():
            raise ValueError("gates.teacher.corpus 必填非空（同语料才可比，§3.4-6）")
        if self.teacher.games <= 0:
            raise ValueError(f"gates.teacher.games={self.teacher.games} 必须 >0（§3.4-7）")
        if not 0 <= self.teacher.wins <= self.teacher.games:
            raise ValueError("gates.teacher.wins 必须在 [0, games] 内")
        _gate_ratio("zero_kill_frac", self.teacher.zero_kill_frac, "gates.teacher")
        _gate_ratio("timeout_frac", self.teacher.timeout_frac, "gates.teacher")

        # ---- spec 级字段（2026-09-11 评审新增）----
        _gate_ratio("baseline_win_rate", self.baseline_win_rate, "gates")
        if self.min_train_hours is not None and self.min_train_hours < 0:
            raise ValueError("gates.min_train_hours 不得为负（小时）")
        # duty 门必须给阈值（与 plateau 必须给 metrics 同理）
        for r in self.rules:
            if r.kind == "duty" and r.min_train_frac is None:
                raise ValueError(f"gates.rules[{r.id}]: duty 必须给 min_train_frac（[0,1]）")
        # min_gain_pp 依赖 baseline（没有参照系的 effect size 是噪声）
        for r in self.rules:
            if r.min_gain_pp is not None and self.baseline_win_rate is None:
                raise ValueError(
                    f"gates.rules[{r.id}].min_gain_pp 需要 gates.baseline_win_rate "
                    "（零样本起点，§12.4 effect size 的参照系）"
                )
        return self

    def budget_warnings(self, max_hours: float, eval_every: int) -> list[str]:
        """§3.4-8 预算可行性（warn-only，不断言）。调用方负责打印。"""
        if self.est_iter_min is None or max_hours <= 0:
            return []
        rounds_needed = max(1, int(self.sustain)) * 2
        iters_needed = rounds_needed * max(1, int(eval_every))
        need_hours = iters_needed * float(self.est_iter_min) / 60.0
        if need_hours > max_hours:
            return [
                f"gates 预算可行性：判定至少需要 {rounds_needed} 评估轮 ≈ {iters_needed} iter "
                f"≈ {need_hours:.2f}h，而 max_hours={max_hours}——门可能在预算内无法触发（§3.4-8）"
            ]
        return []


def _resolve_courses(names: list[str], where: str) -> None:
    """§3.4-5：跨课门引用的 course 必须找得到文件（复用 resolve_course）。"""
    for nm in names:
        try:
            resolve_course(nm)
        except FileNotFoundError as e:
            raise ValueError(f"{where}: 引用课程 '{nm}' 找不到（{e}）") from e


class PlayerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lives: int | None = None
    level: int | None = None


class CourseConfig(BaseModel):
    """课程配置文件（`nn-training/curricula/*.jsonc`）。

    顶层键 1:1 映射 argparse dest；`stages` / `reward` / `ppo_schedule` 为嵌套块。
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 5
    name: str = "unnamed"
    mode: Literal["per-tick", "intent", "goal"] = "per-tick"

    # ---- 环境 ----
    #: str = 关卡范围规格（透传 --stages）；list[StageSpec] = 自定义关（→ 2000+i）
    stages: str | list[StageSpec] = "0-3"

    @field_validator("stages", mode="before")
    @classmethod
    def _stages_before(cls, v: Any) -> Any:
        """smart union 会先试 `str` 并在 list 输入上报类型错；显式预解析消除歧义。"""
        if isinstance(v, list):
            return [StageSpec.model_validate(x) if isinstance(x, dict) else x for x in v]
        return v

    difficulty: str = "hard"
    max_ticks: int = 12000
    seed_rotate: int = 0
    seeds: str = "0-3"
    player: PlayerBlock = PlayerBlock()
    dodge: Literal["", "off", "l0", "god"] = ""

    # ---- 奖励 ----
    reward: RewardBlock = RewardBlock()

    # ---- 模型/优化 ----
    bc: str = "tmp/student-weights-dagger/weights.json"
    freeze: list[str] = []
    freeze_heads: list[str] = []
    lr: float = 3e-4
    epochs: int = 4
    mb: int = 512
    # R5（2026-09-07）：ret 跨 batch 归一开关——课程级，默认 False 即历史行为。
    # True 经 flat_overrides → args → manifest → worker，全串行路径生效。
    normalize_ret: bool = False
    # BC-anchored kickstart（§363）：True = per-tick 缰绳开（ref 取课程 bc 冻结
    # 快照）；默认 False 即历史行为。warmup_iters 课程 plumbing 见下（CLI 早有）。
    kickstart_ref: bool = False
    gamma: float = 0.995
    lam: float = 0.95
    clip_eps: float | None = None
    vf_coef: float | None = None
    ent_coef: float | None = None
    max_grad_norm: float | None = None
    ppo_schedule: list[PpoScheduleEntry] = []

    # ---- F4 熔断（2026-09-06，DECISIONS §339）：ENT 三阈值课程可配 ----
    # 默认值 = rl/breaker.py 常量（0.60 / 8 / 0.5）；热启动课程（BC 蒸馏权重天生低熵）
    # 下调 ent_break（如 p4-onset 的 0.25）收紧保护。配合 breaker 的相对崩塌语义。
    ent_break: float = 0.60
    ent_break_consec: int = 8
    ent_break_max_winrate: float = 0.5

    # ---- 课程结束门（可选；缺席 = 关闭，老课程逐字节不变）----
    gates: GatesSpec | None = None

    # ---- 运行 ----
    iters: int = 15
    max_hours: float = 0.0
    workers: int = 8
    warmup_iters: int = 1
    stream: int = 1
    keep_iters: int = 3
    out: str = "tmp/rl-weights/weights.json"
    traj: str = "tmp/rl-traj"
    backup_dir: str = ""
    backup_prefix: str = ""
    eval_stages: str = ""
    eval_games_per_stage: int = 0
    eval_every: int = 1

    # ------------------------------------------------------------ 派生

    @property
    def is_custom_stages(self) -> bool:
        return isinstance(self.stages, list)

    @property
    def stage_ids(self) -> list[int]:
        """本课程采样的 stage ID 列表。"""
        if isinstance(self.stages, str):
            from rl.course import parse_range

            return parse_range(self.stages)
        return [CUSTOM_STAGE_BASE + i for i in range(len(self.stages))]

    def stages_range(self) -> str:
        """回填 `args.stages` 的范围串（自定义关 = "2000,2001,2002"）。"""
        ids = self.stage_ids
        return ",".join(str(i) for i in ids)

    def custom_stage(self, stage_id: int) -> StageSpec | None:
        """stage_id → 自定义关规格（非自定义关返回 None）。"""
        if not isinstance(self.stages, list):
            return None
        i = stage_id - CUSTOM_STAGE_BASE
        if 0 <= i < len(self.stages):
            return self.stages[i]
        return None

    def stage_json(self, stage_id: int) -> str | None:
        """stage_id → 下发远端的 `stageJson` 串（剥离注释后的纯 JSON，≤4KB）。"""
        spec = self.custom_stage(stage_id)
        if spec is None:
            return None
        payload = {
            "name": spec.name,
            "grid": spec.grid,
            "forces": spec.forces,
            "count": spec.count if spec.count is not None else len(spec.forces),
        }
        if spec.player_spawn is not None:
            payload["player_spawn"] = {"col": spec.player_spawn.col, "row": spec.player_spawn.row}
        if spec.enemy_spawns:
            payload["enemy_spawns"] = [{"col": s.col, "row": s.row} for s in spec.enemy_spawns]
        if spec.spawn_variants:
            payload["spawn_variants"] = [
                {
                    **(
                        {"player_spawn": {"col": v.player_spawn.col, "row": v.player_spawn.row}}
                        if v.player_spawn is not None
                        else {}
                    ),
                    **(
                        {"enemy_spawns": [{"col": s.col, "row": s.row} for s in v.enemy_spawns]}
                        if v.enemy_spawns
                        else {}
                    ),
                }
                for v in spec.spawn_variants
            ]
        s: str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        nb = len(s.encode("utf-8"))
        if nb > STAGE_JSON_MAX_BYTES:
            raise ValueError(
                f"stageJson {nb} 字节 > 上限 {STAGE_JSON_MAX_BYTES}（stage {stage_id}）——"
                "超限回退路径（临时文件 + codeHash）尚未启用"
            )
        return s

    def reward_spec(self):
        """→ `rl.reward_library.RewardSpec`（含 `startLives` 派生注入）。"""
        from rl.reward_library import RewardSpec, ScheduleSpec

        params = dict(self.reward.params)
        # startLives 由 player.lives / difficulty 派生，不占指标向量维（plan §4.1）
        if "startLives" not in params:
            params["startLives"] = float(
                self.player.lives
                if self.player.lives is not None
                else _default_lives(self.difficulty)
            )
        schedule = {
            k: ScheduleSpec(fro=v.fro, to=v.to, until_iter=v.until_iter, mode=v.mode)
            for k, v in self.reward.param_schedule.items()
        }
        return RewardSpec(
            formula=self.reward.formula,
            params=params,
            param_schedule=schedule,
            terminal=dict(self.reward.terminal),
            scheme=self.reward.scheme,
            reward_scale=self.reward.reward_scale,
            allow_extended_funcs=self.reward.allow_extended_funcs,
            builtin=self.reward.builtin,
            terminal_spread=self.reward.terminal_spread,
        )

    def ppo_schedule_dicts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for e in self.ppo_schedule:
            d: dict[str, Any] = {}
            if e.until_iter is not None:
                d["until_iter"] = e.until_iter
            for k in ("lr", "epochs", "mb", "kl_coef", "kl_cap"):
                v = getattr(e, k)
                if v is not None:
                    d[k] = v
            out.append(d)
        return out

    def flat_overrides(self) -> dict[str, Any]:
        """→ argparse dest 的扁平覆盖表。

        只覆盖**配置文件里显式写了的键**（pydantic model_fields_set）——课程没写
        的键交由 rl-config/argparse 默认值接管；显式写了 `seed_rotate: 0` 这类
        「与 CourseConfig 缺省相同」的值也必须覆盖（否则 JSON 里 50 的默认会
        静默存活）。
        """
        explicit = self.model_fields_set
        mapping = {
            "mode": "mode",
            "difficulty": "difficulty",
            "max_ticks": "max_ticks",
            "seed_rotate": "seed_rotate",
            "seeds": "seeds",
            "dodge": "dodge",
            "bc": "bc",
            "lr": "lr",
            "epochs": "epochs",
            "mb": "mb",
            "normalize_ret": "normalize_ret",
            "kickstart_ref": "kickstart_ref",
            "warmup_iters": "warmup_iters",
            "gamma": "gamma",
            "lam": "lam",
            "iters": "iters",
            "max_hours": "max_hours",
            "workers": "workers",
            "stream": "stream",
            "keep_iters": "keep_iters",
            "out": "out",
            "traj": "traj",
            "eval_stages": "eval_stages",
            "eval_games_per_stage": "eval_games_per_stage",
            "eval_every": "eval_every",
            # F4 熔断三阈值（§339 课程可配；缺席=CLI 默认。注意：此前漏映射，
            # 课程 ent_break 0.25 从未落地、一律跑 0.6——2026-09-07 审计发现，
            # 见 §363 补记； benign：相对语义下 0.6 版只丢绝对臂，跌幅臂仍在）。
            "ent_break": "ent_break",
            "ent_break_consec": "ent_break_consec",
            "ent_break_max_winrate": "ent_break_max_winrate",
        }
        out: dict[str, Any] = {}
        for src_key, dst_key in mapping.items():
            if src_key in explicit:
                out[dst_key] = getattr(self, src_key)
        # stages：str 规格 或 自定义关 list 都折叠成 2000+i 范围串
        if "stages" in explicit:
            out["stages"] = self.stages_range()
        if "freeze" in explicit:
            out["freeze"] = list(self.freeze)
        if "freeze_heads" in explicit:
            out["freeze_heads"] = list(self.freeze_heads)
        if self.player.lives is not None:
            out["lives_override"] = self.player.lives
        if self.player.level is not None:
            out["player_level"] = self.player.level
        if "backup_dir" in explicit and self.backup_dir:
            out["backup_dir"] = self.backup_dir
        if "backup_prefix" in explicit and self.backup_prefix:
            out["backup_prefix"] = self.backup_prefix
        return out


def _default_lives(difficulty: str) -> int:
    """difficulty → 默认命数。

    `src/config/difficulty.ts` 现全难度 `startLives: 3`（§130 统一口径）——此处
    只在课程未显式声明 `player.lives` 时兜底，不复制难度表的演进。
    """
    return 3


def load_course(path: str | Path) -> CourseConfig:
    """读 JSONC 课程配置 → `CourseConfig`（pydantic 校验，非法即 raise）。"""
    from rl.jsonc import load as _load_jsonc

    p = Path(path)
    if not p.exists():
        cand = CURRICULA_DIR / f"{path}.jsonc"
        if cand.exists():
            p = cand
        else:
            raise FileNotFoundError(f"课程配置不存在：{path}（亦未在 {CURRICULA_DIR} 下找到）")
    return CourseConfig(**_load_jsonc(str(p)))


def resolve_course(name_or_path: str) -> Path:
    """`--course s-dodge` → `curricula/s-dodge.jsonc`；`--course-file x.jsonc` → 原样。"""
    p = Path(name_or_path)
    if p.exists():
        return p
    cand = CURRICULA_DIR / f"{name_or_path}.jsonc"
    if not cand.exists():
        raise FileNotFoundError(
            f"课程 '{name_or_path}' 不存在（查找 {cand}）；可用："
            f"{[f.stem for f in sorted(CURRICULA_DIR.glob('*.jsonc'))]}"
        )
    return cand


def course_from_args(args) -> CourseConfig | None:
    """argparse Namespace → CourseConfig（未传 --course/--course-file 时返回 None）。

    同时把解析出的课程文件路径挂到 `args.course_path`——远程模式发布 job 时
    需要课程 jsonc 全文快照 + course_fp（sha256 of 文件字节）进 manifest（D13/D14）。
    """
    name = str(getattr(args, "course", "") or "")
    path = str(getattr(args, "course_file", "") or "")
    if name and path:
        raise SystemExit("[run_rl] --course 与 --course-file 互斥，只能给一个")
    if not name and not path:
        return None
    p = resolve_course(path if path else name)
    args.course_path = str(p)
    return load_course(p)


def apply_course(args, course: CourseConfig) -> None:
    """合并优先级 课程 > rl-config > argparse 默认；无 CLI 逐参覆盖（plan §3）。

    副作用：直接改写 args 各字段 + 挂载 `args.course_obj`/`args.course_name`。
    """
    from rl.log import log

    overrides = course.flat_overrides()
    for k, v in overrides.items():
        setattr(args, k, v)
    args.course_obj = course
    args.course_name = course.name
    # 命数/星级/冻结/归档随课程走（导出器 CLI 参数）
    if course.player.lives is not None:
        args.lives_override = course.player.lives
    if course.player.level is not None:
        args.player_level = course.player.level
    log(
        f"[course] {course.name}: mode={course.mode} stages={course.stages_range()} "
        f"iters={course.iters} out={course.out} overrides={len(overrides)} 键"
    )
    if course.mode != getattr(args, "mode", "per-tick"):
        raise SystemExit(
            f"[course] 课程 mode={course.mode} 与启动 mode={getattr(args, 'mode', 'per-tick')} "
            "不一致——课程自带 mode，勿再用 --mode 覆盖"
        )


def stage_json_for_args(args, stage: int) -> str | None:
    """args 携带的课程 → stage 的 stageJson（非自定义关返回 None）。"""
    course = getattr(args, "course_obj", None)
    if course is None:
        return None
    sj: str | None = course.stage_json(stage)
    return sj


def course_cli_conflicts(cli_values: dict, defaults: dict, course: CourseConfig) -> list[str]:
    """课程存在时，显式 CLI 训练参数 vs 课程键的冲突清单（plan §3 fail-loud）。

    argparse 无法区分「用户显式传参」与「吃默认值」——用 `parse_args([])`
    的默认命名空间做基线：凡 CLI 值 ≠ 默认值 且 该键被课程 flat_overrides
    覆盖，即判为用户意图与课程单一事实来源冲突，响亮报错而非静默忽略。
    """
    overridden = set(course.flat_overrides())
    bad: list[str] = []
    for k in sorted(overridden):
        if k == "mode":
            continue  # 模式一致性已在 apply_course 校验
        if k not in cli_values or k not in defaults:
            continue
        if cli_values[k] != defaults[k]:
            bad.append(f"{k}={cli_values[k]!r}（该键由课程配置定义，不能经 CLI 显式传参）")
    return bad


def args_rollout_overrides(args) -> dict[str, str]:
    """课程命数/星级覆盖（导出器 CLI 参数）；无覆盖返回空 dict。"""
    out: dict[str, str] = {}
    lives = getattr(args, "lives_override", None)
    if lives is not None:
        out["lives_override"] = str(lives)
    lvl = getattr(args, "player_level", None)
    if lvl is not None:
        out["player_level"] = str(lvl)
    return out


def echo_config(args, course: CourseConfig | None, it: int = 1) -> None:
    """`--echo-config`：打印生效配置 + 该 iter 的完整奖励计算信息（评审 R1-8）。"""
    import json as _json

    from rl.log import log

    log("=== --echo-config 生效配置（合并后扁平化） ===")
    keys = sorted(k for k in vars(args) if not k.startswith("_"))
    for k in keys:
        v = getattr(args, k)
        if callable(v) or isinstance(v, CourseConfig):
            continue
        log(
            f"  {k} = {_json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float)) else v}"
        )
    if course is not None:
        spec = course.reward_spec()
        from rl.reward_library import build_reward_fn

        fn = build_reward_fn(spec)
        from rl.log import log as _log

        _log(f"=== 奖励（course={course.name}） it={it} ===")
        _log(f"  scheme       = {spec.scheme}  scale={spec.reward_scale}")
        _log(f"  terminal_spread = {spec.terminal_spread}")
        _log(f"  formula      = {spec.formula or f'<builtin:{spec.builtin}>'}")
        if fn._compiled is not None:
            _log(f"  formula_len  = {len(spec.formula)}  ast_depth={fn._compiled.depth}")
            _log(f"  ast_dump     = {fn._compiled.describe()}")
        params = fn.resolve_params(it)
        _log(f"  params(it={it}) = {_json.dumps(params, sort_keys=True)}")
        _log(f"  formula_hash = {spec.identity()}")
        _log(f"  terminal     = {_json.dumps(spec.terminal)}")
        if course.ppo_schedule:
            from rl.schedule import resolve_ppo_schedule

            sch = resolve_ppo_schedule(course.ppo_schedule_dicts(), it)
            _log(f"  ppo_schedule@it{it} = {_json.dumps(sch)}")
        log("=== end echo-config ===")

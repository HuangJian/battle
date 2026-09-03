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


class StageSpec(BaseModel):
    """自定义关卡（`stageData.ts` 13×13 数字瓦格格式，plan §5.1）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    grid: list[list[int]]
    forces: str = "cccccccccccccccccccc"
    count: int | None = None
    player_spawn: Spawn | None = None
    enemy_spawns: list[Spawn] = []

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
    gamma: float = 0.995
    lam: float = 0.95
    clip_eps: float | None = None
    vf_coef: float | None = None
    ent_coef: float | None = None
    max_grad_norm: float | None = None
    ppo_schedule: list[PpoScheduleEntry] = []

    # ---- 运行 ----
    iters: int = 15
    max_hours: float = 0.0
    workers: int = 8
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
    """argparse Namespace → CourseConfig（未传 --course/--course-file 时返回 None）。"""
    name = str(getattr(args, "course", "") or "")
    path = str(getattr(args, "course_file", "") or "")
    if name and path:
        raise SystemExit("[run_rl] --course 与 --course-file 互斥，只能给一个")
    if not name and not path:
        return None
    return load_course(path if path else name)


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

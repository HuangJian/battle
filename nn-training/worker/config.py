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

import os

from pydantic import BaseModel, ValidationError, field_validator, model_validator

import common.distribution
from biz.course_resolve import (
    _LEVEL_ENV_KEYS as _LEVEL_ENV_KEYS,
)
from biz.course_resolve import (
    CURRICULA_DIR as CURRICULA_DIR,
)
from biz.course_resolve import (
    LEVELS_DIR as LEVELS_DIR,
)
from biz.course_resolve import (
    _resolve_courses as _resolve_courses,
)
from biz.course_resolve import (
    course_from_args as course_from_args,
)
from biz.course_resolve import (
    load_course as load_course,
)
from biz.course_resolve import (
    resolve_course as resolve_course,
)
from biz.course_resolve import (
    resolve_level as resolve_level,
)
from biz.course_resolve import (
    resolve_state_init_bank as resolve_state_init_bank,
)
from biz.course_spec import (
    _GATE_FRAC_FIELDS as _GATE_FRAC_FIELDS,
)
from biz.course_spec import (
    _GATE_PP_FIELDS as _GATE_PP_FIELDS,
)
from biz.course_spec import (
    _GATE_REL_FIELDS as _GATE_REL_FIELDS,
)
from biz.course_spec import (
    CUSTOM_STAGE_BASE as CUSTOM_STAGE_BASE,
)
from biz.course_spec import (
    GATE_CROSS_COURSE_KINDS as GATE_CROSS_COURSE_KINDS,
)
from biz.course_spec import (
    GATE_KINDS as GATE_KINDS,
)
from biz.course_spec import (
    GATE_PLATEAU_METRICS as GATE_PLATEAU_METRICS,
)
from biz.course_spec import (
    GATE_SPLIT as GATE_SPLIT,
)
from biz.course_spec import (
    GATE_SPLIT_KINDS as GATE_SPLIT_KINDS,
)
from biz.course_spec import (
    GATE_VERDICTS as GATE_VERDICTS,
)
from biz.course_spec import (
    STAGE_JSON_MAX_BYTES as STAGE_JSON_MAX_BYTES,
)
from biz.course_spec import (
    CourseConfig as CourseConfig,
)
from biz.course_spec import (
    GateRule as GateRule,
)
from biz.course_spec import (
    GatesSpec as GatesSpec,
)
from biz.course_spec import (
    GateTeacher as GateTeacher,
)
from biz.course_spec import (
    ParamSchedule as ParamSchedule,
)
from biz.course_spec import (
    PlayerBlock as PlayerBlock,
)
from biz.course_spec import (
    PpoScheduleEntry as PpoScheduleEntry,
)
from biz.course_spec import (
    RewardBlock as RewardBlock,
)
from biz.course_spec import (
    Spawn as Spawn,
)
from biz.course_spec import (
    SpawnVariant as SpawnVariant,
)
from biz.course_spec import (
    StageSpec as StageSpec,
)
from biz.course_spec import (
    StateInitBlock as StateInitBlock,
)
from biz.course_spec import (
    _default_lives as _default_lives,
)
from biz.course_spec import (
    _gate_ratio as _gate_ratio,
)

# ------------------------------------------------------------------ 下沉（S5 第十刀，2026-09-27）
# 「rl-config.json 文件面」→ `biz/config_file.py`（路径唯一来源 + 安全读取）。
# 「课程配置类面」→ `biz/course_spec.py`（CourseConfig/GatesSpec 及嵌套模型 + gates 常量）。
# 「课程解析面」→ `biz/course_resolve.py`（目录查找 / level 注入 / 字节冻结 / 跨课门校验）。
# 实现搬家、名字留门面 ⇒ 全仓 `from biz.config import …` 一行不改。
from worker.config_file import (
    RL_CONFIG_ENV as RL_CONFIG_ENV,
)
from worker.config_file import (
    read_rl_config_file as read_rl_config_file,
)
from worker.config_file import (
    rl_config_path as rl_config_path,
)


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
    from common.log import log

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
    # ===== 单一 PPO 路径（2026-09-21，plan/accident.plan.md §3）=====
    # PPO 恒在 hub 队列上由 worker 认领执行 ⇒ 训练进程**没有**本机 PPO 能力。三条不变量：
    #   ① 采集与 PPO 的 wave 级重叠（stream）不再存在：恒置 0（PPO 窗口由云 worker 承担，
    #      本机没有 PPO 窗口可重叠）；
    #   ② double-buffer 预采同规置 0；
    #   ③ 本机 PPO 只有 per-tick 实现 ⇒ intent/goal 无路可走：**入口冻结、响亮拒启**
    #      （实现保留，不静默报废能力）。盘上实测 0 门课程在用这两个 mode。
    if getattr(args, "mode", "per-tick") != "per-tick":
        raise SystemExit(
            f"[run_rl] mode={getattr(args, 'mode', 'per-tick')!r} 无远端 PPO 实现（v1 红线只有 "
            "per-tick）——本机 PPO 路径已随 §3 单一 PPO 路径退役，该 mode 入口暂冻结"
            "（实现保留）；见 plan/accident.plan.md §3 的范围发现表"
        )
    args.stream = 0
    args.double_buffer = 0
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
    # ===== it0 键空间保留给 bc 权重基线评估（2026-09-12 用户）：采集/A-eval 的
    # dist 任务键 = {runId}.{it}，显式 --start-it 0 会与基线的 {runId}.0 撞键
    # （权重互覆、任务互吞）。auto 路径（日志末迭代+1）天然 ≥ 1，无需处理。=====
    if getattr(args, "start_it", None) is not None and int(args.start_it) < 1:
        raise SystemExit(
            "[run_rl] --start-it 必须 ≥ 1（0 保留给 it0 bc 基线评估的 dist 键空间）："
            f"{args.start_it}"
        )


# 语料身份指纹（D14）—— 2026-09-30 下沉到 `biz/corpus_fp.py`（它属游戏域，而本模块要搬进 `worker/`）。
# 这里保留 re-export：既有 20+ 处 `from worker.config import corpus_identity_fp` 一个字不用改。
from biz.corpus_fp import (
    corpus_identity_fp as corpus_identity_fp,
)


def apply_course(args, course: CourseConfig) -> None:
    """合并优先级 课程 > rl-config > argparse 默认；无 CLI 逐参覆盖（plan §3）。

    副作用：直接改写 args 各字段 + 挂载 `args.course_obj`/`args.course_name`。
    """
    from common.log import log

    overrides = course.flat_overrides()
    for k, v in overrides.items():
        setattr(args, k, v)
    args.course_obj = course
    args.course_name = course.name
    # 缰绳初值 vs 缰绳开关自洽（§5.1）：声明了初值却没开缰绳 = 配置自相矛盾
    # （那个数会被静默丢弃；反过来人也以为自己在勒缰绳）。启动期响亮拒，不静默带过。
    _ki = course.kickstart_init
    if _ki is not None and float(_ki) > 0 and not bool(getattr(args, "kickstart_ref", False)):
        raise SystemExit(
            f"[course] {course.name}: kickstart_init={_ki} 但 kickstart_ref 未开 —— "
            "缰绳没开，初值无消费方（要么两个都写，要么 kickstart_init 写 0）"
        )
    # ── 起始分布（plan/x20-state-init.plan.md P2）：声明了就必须**能跑**。快照银行不在盘上
    # 就在启动期响亮拒——它是 P0 的产物，而不是「以后再补」的路径；静默退回标准开局等于
    # 换了一个实验（而日志/账本还以为自己跑的是中段起跑那条腿）。
    si = course.state_init
    if si is not None:
        if not si.bank.strip():
            raise SystemExit(
                f"[course] {course.name}: state_init 声明了但没给 bank —— 起始分布没有快照"
                "银行就是空转（要么删掉整个 state_init 块，要么写 bank 路径）"
            )
        bank = resolve_state_init_bank(si.bank)
        if bank is None:
            raise SystemExit(
                f"[course] {course.name}: state_init.bank 不在盘上（{si.bank}）——"
                "快照银行是 plan/x20-state-init.plan.md P0 的产物"
                "（manifest.json + snapshots/）；先把它建出来（P0 的 tickHash 对账过了才算），"
                "再开这条腿"
            )
        log(
            f"[course] state_init 起始分布：bank={bank}，切点 [{si.cut_from}, {si.cut_to}"
            f"（负=从局尾回退）] 步 {si.cut_step}，rotate_cuts={si.rotate_cuts}"
            "（切点由 P0 物化进 manifest.cuts[]，P3 按 key 抽——选哪个状态进语料身份）"
        )
    # 进程级课程身份导出（v5 多课程，2026-09-18）：出站的权重上报/任务下发都带它，
    # agent 侧按 (course, kind) 分桶。住在这里是因为这是训练进程**唯一**知道课程名的
    # 地方（args.course_name 刚被挂上），而出口散在 6 个文件的不同闭包里。
    os.environ[common.distribution.COURSE_ENV] = course.name
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


# ────────────── 多课程本机并发配额（plan multi-course-parallel-training §3.4 / P4-W1） ──
# 机器配额只住 rl-config 的 `courses.<课>` 块，**永不写进 curricula/*.jsonc**：课程文件
# 参与 course_fp 血缘（D14），改一下配额就让熔断把同一份语料误判成新语料。课程文件里
# 的 `workers` 是「课程声明」，`courses.<课>.workers` 是「本机实际切分」；两者分叉时
# 必须打响亮行（C1），不能静默顶替。后来者：不要把覆盖逻辑"顺手"搬进 apply_course——
# 那会让课程覆盖与机器配额重新纠缠（plan C1 ③）。


def course_key_of(args) -> str:
    """课程命名空间键（课程文件 stem）；无课程 → ''。与 TS slots.ts / 账本同键。"""
    try:
        from worker.train.loop_util import course_key_from_path

        return course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except Exception:
        return ""


def resolve_course_quota(
    dist_cfg: dict | None,
    course_key: str,
    workers: int,
    local_slots: int,
) -> tuple[int, int, str | None]:
    """按课程热读覆盖本机并发配额（纯函数）。返回 (workers, local_slots, 响亮行)。

    优先级：`courses.<课>.{workers,local_slots}` > `rl.{workers,local_slots}` > 现状值。
    `workers` 变化时返回 `[quota] workers 8 -> 4 (multi-course split)`（DoD 断言其
    存在）；无覆盖 → 原值 + None。0 是合法值（语义 = 关闭本课本机直跑）。
    """
    cfg = dist_cfg or {}
    rl_block = cfg.get("rl") or {}
    cblock = ((cfg.get("courses") or {}).get(course_key) or {}) if course_key else {}
    if not isinstance(cblock, dict):
        cblock = {}
    new_workers = int(workers)
    loud: str | None = None
    hot_workers = cblock.get("workers")
    if hot_workers is not None and int(hot_workers) != int(workers):
        loud = f"[quota] workers {workers} -> {hot_workers} (multi-course split)"
        new_workers = int(hot_workers)
    hot_ls = cblock.get("local_slots", rl_block.get("local_slots"))
    new_ls = int(local_slots) if hot_ls is None else int(hot_ls)
    return new_workers, new_ls, loud


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

    from common.log import log

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
        from biz.reward_library import build_reward_fn

        fn = build_reward_fn(spec)
        from common.log import log as _log

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
            from worker.schedule import resolve_ppo_schedule

            sch = resolve_ppo_schedule(course.ppo_schedule_dicts(), it)
            _log(f"  ppo_schedule@it{it} = {_json.dumps(sch)}")
        log("=== end echo-config ===")

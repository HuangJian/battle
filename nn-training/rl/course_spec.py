"""rl/course_spec —— **课程配置的类面**（S5 第十刀，2026-09-27）。

从 `rl/config.py` 整块搬出（**逐字节不动**）。这里是 `curricula/*.jsonc` / `levels/*.jsonc`
在代码里的**形状**：全部 pydantic 模型（`CourseConfig` 及其嵌套块 · `StageSpec` ·
`RewardBlock` · `PpoScheduleEntry` · `GatesSpec` 一族）与 gates 声明面（catalog / 判决字面量 /
阈值分档常量 + 解析期强校验）。

**为什么单独成家**：类面（一份配置**长什么样、什么时候拒收**）与「解析面」（去哪找文件、
level 怎么注入、字节怎么冻结）、「文件面」（rl-config.json 在哪）互不纠缠——加一个课程字段
只需看这一个文件；`rl/config.py` 保留 `X as X` 门面 ⇒ 历史 import 一行不改。

依赖面 = stdlib（`json` / `math` / `typing`）+ `pydantic` + `rl.reward_library`（`OUTCOMES` 词表）。
唯一一条向上的引用是 `GatesSpec` 校验跨课门时的**函数内**延迟导入
`rl.course_resolve._resolve_courses`（顶层 import 会成环；静态边方向：解析面 → 类面）。
⚠ 谁改门校验的跨课引用检查，别把这条延迟导入提到模块顶层。
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from rl.reward_library import OUTCOMES

# ================================================================== 课程配置
#
# `--course <name>` —— 课程 = 启动参数 + 关卡布局 + 奖励公式的单一事实来源
# （plan/rl-training-config.md）。合并优先级：**课程配置 > rl-config.json >
# argparse 默认**，且**没有**逐参数 CLI 覆盖通道（评审 P1-7：改名 --course 避开
# 既有 --curriculum-stages/--start/--every/--grow 的语义冲突）。
#
# 格式 = JSONC 子集（只 `//` 行注释，见 rl/jsonc.py）；`extra="forbid"` —— 拼错
# 的键直接响亮报错，不静默忽略。

#: stageJson 查询串上限（评审 LC §4.4）：13×13 grid ~700 字节，4KB 留足余量
STAGE_JSON_MAX_BYTES = 4096
#: 自定义关（配置内 grid）起始 ID：第 i 个 → 2000+i（plan §5.2）
CUSTOM_STAGE_BASE = 2000


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
    # 熵正则系数（2026-09-11 接线，缺省 None = 引擎常量 ENT_COEF=0.01）。
    # 动机：per-tick 线熵坍缩（唯一学动的 c4-kb1 熵 0.71–0.74，其余四条卡 0.44–0.51）。
    ent_coef: float | None = None


# ---------------------------------------------------------------------------
# 课程结束门（plan/course-exit-and-shutdown.md §3；DECISIONS §337/§338）
# ---------------------------------------------------------------------------
#
# 顶层可选块 `"gates"`：门从课程注释走进代码。缺席 = 关闭（老课程逐字节不变）。
# 阈值随课程文件进 course_fp（§331-D14）——改阈值 = 新实验版本。
#
# 本模块只管**声明与解析期校验**（M0）；求值器在 `rl/gate_check.py`（M1），
# in-loop 接线在 `rl/loop_guards_gate.py` 第四守卫（M1；S4 第二十三刀前住 rl/loop_guards.py）。

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
    # 判决力（2026-09-11）：池化判据 + 噪声带——逐点判在 100 局/点下 SE=4.3pp，
    # 根本分辨不出 min_gain_pp=5pp（细节见 docs/agents.details.md 无关；依据在
    # gates 块的 pool_window/conf_z 注释与 tests/test_gate_check.py）。
    #: 用最近 N 个评估点**合并局数**判（p=Σwins/Σgames，SE=√(p(1-p)/N)）。
    #: None = 逐点判（历史行为，其它课程零变化）。
    pool_window: int | None = None
    #: 效应须高于噪声：p − conf_z×SE ≥ baseline + min_gain_pp/100（需 pool_window≥1）。
    conf_z: float | None = None
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
    #: ADVANCE 附加条件（§12.4：数据还不够下结论 ≠ 事故）：累计**样本通过量**
    #: Σ(samples × epochs) 不足此值 → HOLD，不下 ADVANCE/STOP 判决。
    #: 这是"证据充分性"的直接度量：与硬件/网络/事故无关，且能在开腿前按
    #: seed_rotate × 样本/局 × epochs × iters 预先算出。
    #: （2026-09-11 用它取代了 `min_train_hours`：时间口径夹带打包上传/排队/下载，
    #:  排队越久越"达标"，而本地采样期间云端空转——照烧 Kaggle 配额——它又看不见。
    #:  事故拦截归 G13 duty。）
    min_train_samples: float | None = None
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
                # 延迟导入（S5 第十刀）：实现住解析面；顶层 import 会与
                # course_resolve → course_spec 的静态边成环。
                from rl.course_resolve import _resolve_courses

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
        if self.min_train_samples is not None and self.min_train_samples <= 0:
            raise ValueError("gates.min_train_samples 必须 >0（样本通过量）")
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
        # 池化判据 + 噪声带（2026-09-11 判决力修复）
        for r in self.rules:
            if r.pool_window is not None:
                if r.kind != "wins_mastery":
                    raise ValueError(
                        f"gates.rules[{r.id}]: pool_window 只适用于 wins_mastery"
                        "（池化读的是 games/wins 合并局数）"
                    )
                if int(r.pool_window) < 1:
                    raise ValueError(f"gates.rules[{r.id}].pool_window 必须 ≥1")
            if r.conf_z is not None:
                if int(r.pool_window or 0) < 1:
                    raise ValueError(
                        f"gates.rules[{r.id}].conf_z 需要 pool_window≥1"
                        "（单点 100 局的 SE≈4.3pp，撑不起显著性判定）"
                    )
                if float(r.conf_z) < 0:
                    raise ValueError(f"gates.rules[{r.id}].conf_z 不得为负")
                if r.min_gain_pp is None or self.baseline_win_rate is None:
                    raise ValueError(
                        f"gates.rules[{r.id}].conf_z 需要 min_gain_pp + gates.baseline_win_rate"
                        "（要比的正是「高于起点的效应量」）"
                    )
        return self

    def power_notes(self, games_per_point: int) -> list[str]:
        """§12.4 判决力（warn-only）：门要求的效果量必须**大于自身噪声**才可能判出来。

        判据 `SE(p) = √(p(1-p)/N) ≤ min_gain_pp/2`（效果 ≥ 2×标准误），
        N = games_per_point × pool_window（池化把多个评估点的局数合并）。
        games_per_point<=0（还没有 summary 行）→ 不判、不告警。
        """
        if int(games_per_point) <= 0:
            return []
        out: list[str] = []
        for r in self.rules:
            if r.kind != "wins_mastery" or r.min_gain_pp is None:
                continue
            delta = float(r.min_gain_pp) / 100.0
            p = min(0.999, float(self.baseline_win_rate or 0.0) + delta / 2.0)
            window = max(1, int(r.pool_window or 1))
            n = int(games_per_point) * window
            se_pp = 100.0 * math.sqrt(p * (1.0 - p) / n)
            if se_pp <= float(r.min_gain_pp) / 2.0:
                continue
            need = math.ceil(4.0 * p * (1.0 - p) / (delta * delta)) if delta > 0 else 0
            out.append(
                f"gates 判决力({r.id}): 要求 {r.min_gain_pp:g}pp，但 {n} 局"
                f"（{games_per_point}×pool_window={window}）的 SE={se_pp:.1f}pp >"
                f" {float(r.min_gain_pp) / 2.0:g}pp —— 该门分辨不出自己要求的效果（§12.4）："
                f"开 pool_window、把 eval_games_per_stage 提到 ≥{need}，或降 min_gain_pp"
            )
        return out

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


class PlayerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lives: int | None = None
    level: int | None = None


class StateInitBlock(BaseModel):
    """课程 `state_init` 块：rollout 的**起始分布** = 人类 demo 中段**世界快照**交棒
    （plan/x20-state-init.plan.md；课程 `curricula/x20-state-init.jsonc`）。

    语义（TS 侧执行，P1 落地；银行由 P0 产出）：一局仍是标准的 `(stage, seed)` 游戏，但起始世界
    换成人类 demo 在 tick `T` 的 `cloneWorld` 快照（人类**真正到达过**的状态），`T` 之后交棒
    给策略。游戏时钟继续走（`max_ticks` 照旧对游戏时钟算，cut 吃掉 `T`）。PPO/GAE 不动
    （中段开局数学上等价于一次截断续跑，truncated bootstrap 现成）。

    为什么是快照而不是「人类磁带快进」（首版设计，已否决，plan §2 B1/B2/B5/B6）：用输入重建
    出来的不是人类那个状态——idle 语义（人类 20–30% 帧静止）、RNG 分叉、FF 段可能先死——而
    快照把它变成了一个纯注入问题，且每个切点都有录像 tickHash 做**外部**证据。

    **缺席（`state_init is None`）= 现状逐字节不变**：这是纯增量块，老课程一个字都不受影响。
    子字段全可选，缺省 = v1 口径（值与课程文件同源，见各自注释）；语义上**必须给 `bank`**
    ——没有银行就没有起始分布，`apply_course` 在启动期响亮拒（不静默退回标准开局：那是另一个
    实验，不是这个）。
    """

    model_config = ConfigDict(extra="forbid")

    #: 快照银行 manifest（P0 产物，`{recipe, games:[{id, stage, demoSeed, ticks, cuts[]}]}`）
    #: + 同目录 `snapshots/<stage>-<demoSeed>-t<tick>.json`。仓库相对（`nn-training/data/...`）
    #: 与 nn-training 相对两种写法都认——见 `resolve_state_init_bank`。
    bank: str = ""
    #: 切点下界（局内步数）：排除开局这一段——标准起点已经覆盖了它。
    cut_from: int = 300
    #: 切点上界：**负值 = 从局尾回退**（`-120` ⇒ 排除终局前 120t 的 trivial 态）。
    cut_to: int = -120
    #: 切点步长（同一局上可选交棒时刻的间隔）。
    cut_step: int = 300
    #: True = 每轮轮换切点（派生 key 带 `(runSeed, it, stage, seed)`：resume-safe、每轮换，§15.1）。
    rotate_cuts: bool = True

    # ⚠ `cut_from/cut_step` 必须同时是 `K`（决策间隔 10）与 `hashInterval`（tickHash 采样 100）的
    # 整数倍：前者保证交棒点落在决策边界上，后者保证每个快照都有录像 tickHash 可对账。两条
    # 都在 **P0 物化时**响亮校验（`tools/sim/build-state-init-bank.ts`）——量纲常量的单一来源在
    # TS 侧，这里不复写一份（避免两处漂移）。

    @model_validator(mode="after")
    def _check_cuts(self) -> StateInitBlock:
        if self.cut_from < 0:
            raise ValueError(f"state_init.cut_from={self.cut_from} 不得为负（切点是局内步数）")
        if self.cut_to > 0:
            raise ValueError(
                f"state_init.cut_to={self.cut_to} 必须 ≤0（负值 = 从局尾回退；'到局尾' 写 0）。"
                "绝对上界要读银行读数才知道局有多长，v1 只支持相对局尾"
            )
        if self.cut_step < 1:
            raise ValueError(f"state_init.cut_step={self.cut_step} 必须 ≥1（切点步长）")
        return self


class CourseConfig(BaseModel):
    """课程配置文件（`nn-training/curricula/*.jsonc`）。

    顶层键 1:1 映射 argparse dest；`stages` / `reward` / `ppo_schedule` 为嵌套块。
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 5
    name: str = "unnamed"
    mode: Literal["per-tick", "intent", "goal"] = "per-tick"
    #: 关卡引用（`nn-training/levels/<name>.jsonc` 或路径）。设置后 stages/difficulty/
    #: max_ticks/player 四类环境键**只能**来自关卡文件（load_course 合并；课程侧重复
    #: 声明 = 配置冲突 raise）。缺席 = 内联 stages 旧用法，逐字节兼容。
    level: str = ""

    # ---- 环境 ----
    #: str = 关卡范围规格（透传 --stages）；list[StageSpec] = 自定义关（→ 2000+i）
    stages: str | list[StageSpec] = "0-3"

    @field_validator("target_transitions", "est_samples_per_game", "max_games_per_stage")
    @classmethod
    def _volume_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("动态采集键非法（≥0；0 = 关闭/默认规则）")
        return v

    @model_validator(mode="after")
    def _check_volume_keys(self) -> CourseConfig:
        """`target_transitions > 0` 必须带局均 **samples** 估计（D1：反解没有估计值 = 乱采）。"""
        if self.target_transitions > 0 and self.est_samples_per_game <= 0:
            raise ValueError(
                "target_transitions > 0 时必须给 est_samples_per_game（≥1，单位=samples/局"
                "= 局均 ticks / K）——配额反解初波局数需要与账本同单位的估计"
                "（旧键名 est_ticks_per_game 是 10× 量纲错，2026-09-15 T9 改名）"
            )
        return self

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
    #: 配对双臂共用的 rotateSeed（2026-09-21，plan/accident.plan.md §2）。
    #: 配对比较要求两臂**同 rotateSeed**（`(rotateSeed, it)` 种子流逐轮一致 = McNemar 前提）。
    #: 靠人手在命令行传会漏会错（实测 1789926833 vs 1789926915，差 82 秒抖动 ⇒ 配对失败返工）
    #: ⇒ 值进**课程文件**（设计时写、可复现、可评审、可追溯），控制台开课原样透传。
    #: `None`（缺席）= 老行为逐字节不变（`resolve_rotate_seed` 的继承/抖动三级）。
    #: ⚠ 与 `seed_rotate` 一字之差、语义天壤（那个是“每关抽几局”的旧旋钮）——命名刻意区分。
    #: ⚠ 经 `flat_overrides` 映射到 argparse dest `rotate_seed`（课程键与 dest 允许异名）；
    #: 写 `null` 与**不写**不等价（`flat_overrides` 只看 `model_fields_set`：显式 null 会
    #: 透传覆盖 ⇒ 调 CLI 后门请在课程文件里**删键**，不要写 null）。
    paired_rotate_seed: int | None = None
    # ---- 按样本量动态采集（plan/dynamic-rollout-volume.plan.md；缺席 = 老行为逐字节不变）----
    #: 每轮目标 transitions（已结算 shard 的 nSamples 之和；分关达标线 = ceil(/关数)）。
    #: 0 = 关闭：走 seed_rotate 固定局数旧语义，`rl/volume_waves.py` 一个函数都不被调用。
    #: 进 corpus_identity_fp（量纲变更 = 采样参数变更，与 seed_rotate 同待遇）。
    target_transitions: int = 0
    #: 局均 **samples** 估计（= 局均 ticks / K；首轮/无历史时反解局数用；之后由
    #: trailing 均值覆盖）。与 `target_transitions` 同单位 = 已结算 shard 的 nSamples。
    #: `target_transitions > 0` 时必填——配额反解没有估计值就是静默乱采（响亮报错）。
    #: ⚠ 旧键名 `est_ticks_per_game`（ticks 填进 samples 分母 = 10× 误采，评审复算
    #: 「兑现 37% 触顶」）已停用；extra="forbid" ⇒ 照写旧键会启动期响亮报错。
    est_samples_per_game: int = 0
    #: 单关单轮局数硬顶（0 = 默认规则：初波 G0 × DEFAULT_GAME_CAP_MULT）。
    #: 防短局 pathological 下局数爆炸；触顶 = 配额未满但停采 + 响亮日志。
    max_games_per_stage: int = 0
    seeds: str = "0-3"
    player: PlayerBlock = PlayerBlock()
    dodge: Literal["", "off", "l0", "god"] = ""
    #: rollout **起始分布**（plan/x20-state-init.plan.md）：人类 demo 磁带中段交棒。
    #: 缺席 = 标准开局（老课程逐字节不变）。开训前置（银行 manifest 在盘上）由
    #: `apply_course` 在启动期校验——`load_course` 不多读盘（它只读课程文件本身）。
    state_init: StateInitBlock | None = None

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
    #: 缰绳的**显式初值** `kk(1)`（plan/accident.plan.md §5.1，2026-09-21）。
    #: 公式写死 `kk(it) = kickstart_init × decay^(it-1)`（run 原点，`loop_steps.kickstart_coef`）
    #: ——**不**嗅探 `*.it<N>.*` 文件名判 continuation（脆弱，且违反「判据要有稳定锚点」）。
    #: 缺席 = 沿用 rl-config/argparse 的 `kickstart_kl`（现状行为逐字节不变）。
    #: 为什么要有这个键：C 事故那条腿没声明初值，拿到的就是缺省 kk=1 —— 满额复活、
    #: 锚主导更新连烧 30 轮（it1 kl=0.90）。显式声明既让意图可读，也让启动自检
    #: 能区分「课程写的」与「吃缺省」（缺省大值 = 响亮 warning）。
    #: ⚠ 经 `flat_overrides` 映射到 argparse dest `kickstart_kl`（异名）；
    #: 它**不进** `corpus_identity_fp`（ref/优化器语义 ≠ 「一个样本是什么」，进去会让
    #: 全体课程指纹漂移）——见 `tests/test_kickstart_plan.py` 的断言。
    kickstart_init: float | None = None
    #: demo 混 batch（x20 后续）：demo bank npz 路径（仓库相对，如
    #: `nn-training/data/human-x20-corpus/demo_bank.npz`）；"" = 关闭，老行为逐字节不变。
    #: 与 kickstart_init 同待遇：**不进** `corpus_identity_fp`（loss 侧数据 ≠ 「rollout
    #: 样本是什么」；course_fp 照常覆盖整文件字节，行为变了就该是新实验）。
    demo_bank: str = ""
    #: demo BC 辅 loss 系数（loss += coef · (CE_move + CE_fire)）；0.0 = 关闭。
    #: 量级锚：CE 均值 ~1.0，PPO 侧 value 项 ~0.25 ⇒ 0.02 ≈ 8% 梯度占比起步。
    demo_bc_coef: float = 0.0
    #: 每 PPO minibatch 步抽的 demo 样本数（np RNG，ckpt 精确复现）；0 = 关闭。
    demo_per_mb: int = 0
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
            for k in ("lr", "epochs", "mb", "kl_coef", "kl_cap", "ent_coef"):
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
            # 配对 rotateSeed：课程键 → argparse dest（§2；漏映射 = 静默失效，ent_break 前科）
            "paired_rotate_seed": "rotate_seed",
            # 动态采集三键（缺席 = 老行为：args 走 rl-config/argparse 默认值 0）
            "target_transitions": "target_transitions",
            "est_samples_per_game": "est_samples_per_game",
            "max_games_per_stage": "max_games_per_stage",
            "seeds": "seeds",
            "dodge": "dodge",
            "bc": "bc",
            "lr": "lr",
            "epochs": "epochs",
            "mb": "mb",
            "normalize_ret": "normalize_ret",
            "kickstart_ref": "kickstart_ref",
            # demo 混 batch 三键（缺席 = 老行为：args 走 getattr 缺省，manifest 无键）
            "demo_bank": "demo_bank",
            "demo_bc_coef": "demo_bc_coef",
            "demo_per_mb": "demo_per_mb",
            # 缰绳初值 kk(1)：课程键 → argparse dest（异名；漏映射 = 静默失效，ent_break 前科）
            "kickstart_init": "kickstart_kl",
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
        if "state_init" in explicit and self.state_init is not None:
            # 嵌套块 → **dict**（不进上面的 mapping：那一张是标量与异名映射表）。下游
            # （P3 的 `rl/cmd.build_rollout_cmd`）只要 JSON 可序列化的数据——`echo_config`
            # 也会 json.dumps 它，pydantic 模型对象在那里直接炸。
            out["state_init"] = self.state_init.model_dump()
        return out


def _default_lives(difficulty: str) -> int:
    """difficulty → 默认命数。

    `src/config/difficulty.ts` 现全难度 `startLives: 3`（§130 统一口径）——此处
    只在课程未显式声明 `player.lives` 时兜底，不复制难度表的演进。
    """
    return 3

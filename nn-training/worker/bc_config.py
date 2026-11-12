"""biz/bc_config.py — BC 课程配置（`curricula/<name>.bc.jsonc`）。

BC（行为克隆）课程独立于 RL 的 `CourseConfig`（plan/bc-cloud-integration.plan.md §2）：
mode 红线 / gates / ppo_schedule / 热加载字段全不适用，且 `CourseConfig.extra="forbid"`
会拒绝 BC 专有键。独立文件种类 `.bc.jsonc` 让控制台/编排器一眼分流。

与 CourseConfig 共享的语义：
  - JSONC 子集（`//` 行注释，common/jsonc.py）；`extra="forbid"` 拼错即响亮报错；
  - `"level"` 关卡引用：stages/difficulty/player 三类环境键**只能**来自关卡文件
    （levels/*.jsonc），课程侧重复声明 = 配置冲突 raise（DECISIONS
    §2026-09-13-level-extraction 同规则）；四类环境覆盖（`stage_select` / `enemies` /
    `lives` / `star`）与 `max_ticks` 照写可用——两条加载链走**同一份** `apply_level_file`
    （2026-10-28，docs/nn/training-stack.md §49）；
  - D14 语料身份：`bc_corpus_identity_fp` = 语料决定字段（env + corpus 参数）的解析值
    语义哈希；train 超参 / 预算 / 路径刻意排除——改它们不构成语料变化。

本模块 torch-free / numpy-free（trainer/run_bc.py 与单测在无 torch 环境导入）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from biz.course_resolve import apply_level_file
from biz.course_spec import CustomStagesMixin
from common.hashing import sha256_file

REPO_ROOT = Path(__file__).resolve().parents[2]
CURRICULA_DIR = Path(__file__).resolve().parent.parent / "curricula"
LEVELS_DIR = Path(__file__).resolve().parent.parent / "levels"

BC_SUFFIX = ".bc.jsonc"


class BcPlayerBlock(BaseModel):
    """语料采集的玩家口径（命数/星级覆盖 → 导出器 livesOverride/playerLevel）。"""

    model_config = ConfigDict(extra="forbid")

    lives: int | None = None
    level: int | None = None


class BcCorpusTarget(BaseModel):
    """目标占比（`outcome_policy=ratio` 时必填；三数和 = 1，win > 0）。"""

    model_config = ConfigDict(extra="forbid")

    win: float = 0.0
    loss: float = 0.0
    timeout: float = 0.0

    @field_validator("win", "loss", "timeout")
    @classmethod
    def _fraction(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("corpus.target 的每一项必须在 [0, 1]")
        return v

    @model_validator(mode="after")
    def _sums_to_one(self) -> BcCorpusTarget:
        total = self.win + self.loss + self.timeout
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"corpus.target 三项之和必须为 1，收到 {total!r}")
        if self.win <= 0:
            raise ValueError("corpus.target.win 必须 > 0（胜局是比例锚点）")
        return self


class BcCorpusBlock(BaseModel):
    """God-AI 教师语料生成参数（→ export-godai-bc / biz/bc_dispatch）。"""

    model_config = ConfigDict(extra="forbid")

    #: collect = 派发采集；fixed = 直接读 `fixed_dir`（不采集、不筛结局）。
    source: Literal["collect", "fixed"] = "collect"
    #: all = 全留（`wins_only=true` 时仍是历史 wins-only）；ratio = 目标占比。
    outcome_policy: Literal["all", "ratio"] = "all"
    #: ratio 的目标占比；`fixed`/`all` 不得配置。
    target: BcCorpusTarget | None = None
    #: ratio 的配额粒度：stage = 逐关独立；global = 全课程统一。
    scope: Literal["stage", "global"] = "stage"
    #: fixed 的语料根；空串 = 课程 `data_dir`。不参与语料指纹。
    fixed_dir: str = ""
    #: 每关局数（轮 r 的种子 = [1 + (r-1)*seed_rotate, ... + games_per_stage)，§15.1 轮转）
    games_per_stage: int = 40
    #: wins-only（历史口径：仅保留 stage_clear 局的 shard；败局 = 合法"跳过"结果）。
    #: `ratio` 必须为 false；`fixed` 也必须为 false（固定语料原样使用）。
    wins_only: bool = True
    #: 守家帧超采样倍数（export-godai-labels 同款；0/1 = 关）
    near_miss_times: int = 3
    #: v6 标签来源：argmax（默认，压熵）/ sampled（保留教师采样）。
    #: **仅 `action_v6` 时进语料身份**——legacy 课程不受影响。
    label_mode: Literal["argmax", "sampled"] = "argmax"
    #: v6 标签策略权重（仓库根相对或绝对）；仅 v6 采集课必填。
    #: 权重内容 sha256 进 `bc_corpus_identity_fp`，换权重 = 新语料。
    label_policy: str = ""
    #: 语料轮转步长（轮 r 种子起点 = 1 + (r-1)*seed_rotate；0 = 每轮同种子，仅单轮课用）
    seed_rotate: int = 0

    @field_validator("games_per_stage", "near_miss_times", "seed_rotate")
    @classmethod
    def _nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("corpus 计数类字段必须 ≥ 0")
        return v

    @model_validator(mode="after")
    def _policy_consistent(self) -> BcCorpusBlock:
        """互斥关系在加载时响亮报错；运行期不再猜配置意图。"""
        if self.source == "fixed":
            if self.outcome_policy != "all":
                raise ValueError("corpus.source=fixed 只允许 outcome_policy=all")
            if self.target is not None:
                raise ValueError("corpus.source=fixed 不接受 target（固定语料不筛选）")
            if self.wins_only:
                raise ValueError("corpus.source=fixed 必须显式 wins_only=false（原样使用）")
            if self.games_per_stage != 0:
                raise ValueError("corpus.source=fixed 必须 games_per_stage=0（不采集）")
        elif self.outcome_policy == "ratio":
            if self.wins_only:
                raise ValueError("outcome_policy=ratio 与 wins_only=true 冲突")
            if self.target is None:
                raise ValueError("outcome_policy=ratio 必须配置 corpus.target")
        elif self.target is not None:
            raise ValueError("corpus.target 只用于 outcome_policy=ratio")
        return self


class BcEvalBlock(BaseModel):
    """BC 每 N epoch 的多地图干净评估（对齐 RL eval 展示口径：
    win_rate / kills_mean / phits_mean / pickup_mean / timeout_frac）。

    levels = 关卡文件名列表（`nn-training/levels/<name>.jsonc` 或路径）——eval 地图
    与训练语料的 level 解引用**互不约束**（bc-c4 训 arena4、eval arena4+arena6 是
    合法配置）；每张图按其关卡文件的 stages/difficulty/max_ticks/player 逐 stage
    × games_per_stage 派 mode=eval 任务（策略 = 被评 BC 权重本身）。"""

    model_config = ConfigDict(extra="forbid")

    #: 每 N epoch 评估一次；0 = 关（默认——不配置 eval 块行为零变化）
    every_epochs: int = 0
    #: 每张图每个 stage 的评估局数（种子 1..games_per_stage）
    games_per_stage: int = 10
    #: 评估地图（关卡引用；多张图各自出结果，控制台分图展示）
    levels: list[str] = []

    #: **直接继承课程环境**（2026-11-12）：True ⇒ 忽略 `levels`，用课程自己解析后的
    #: stage 集与四类覆盖（`stage_select` / `enemies` / `lives` / `star` + `max_ticks`），
    #: 与训练语料逐字同源。只支持 `course.stages` 为 list 的课程（range 型没有逐关
    #: stageJson，云端会把 stage 号当真实关跑）。与 `levels` 同时配置 ⇒ 加载时拒。
    course_env: bool = False

    @field_validator("every_epochs", "games_per_stage")
    @classmethod
    def _nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("eval 计数类字段必须 ≥ 0")
        return v

    @model_validator(mode="after")
    def _course_env_excludes_levels(self) -> BcEvalBlock:
        """两份关集事实不可同时存在（否则 eval 面板说不清是课程还是关卡文件）。"""
        if self.course_env and self.levels:
            raise ValueError(
                "eval.course_env=true 与 eval.levels 互斥：课程环境已含关集，不要另写 levels"
            )
        return self

    @property
    def enabled(self) -> bool:
        return (
            self.every_epochs > 0
            and self.games_per_stage > 0
            and (bool(self.levels) or self.course_env)
        )


class BcTrainBlock(BaseModel):
    """BC 训练超参（→ 云端 train/bc.py train() 的 SimpleNamespace 参数）。"""

    model_config = ConfigDict(extra="forbid")

    #: 'bc' = NNPolicy（双头），'student' = CoordConv-ConvMixer-Lite（蒸馏主线）
    arch: Literal["bc", "student"] = "student"
    #: 训练上限（B3）：终止由早停/行为门决定，不是「必须跑满」。
    epochs: int = 60
    #: 早停耐心（0 = 关）：滑动平均 val_loss 连续这么多 epoch 没改善就停。
    #: 单点新低被 fire 头噪声主导（与 fire_acc 相关 −0.965），不用它做「还有没有收益」。
    patience: int = 0
    batch: int = 512
    lr: float = 3e-3
    #: mirrorX 在线增强概率（训练 split）
    mirror_p: float = 0.5
    #: shard 级 train/val 切分比例
    val_split: float = 0.1
    #: >0 且语料带 returns.npy 时对 value 头做 MC 回归（历史教训：默认 0——
    #: value MC 回归在 coef 0.05 下都爆，docs/nn/legacy.md §20 M3）
    value_coef: float = 0.0
    #: C9 value-only 预热：策略 CE 权重。0 = 冻结 trunk/策略头，只训 value_head；
    #: 1 = 历史 BC。0 必须配 value_coef > 0（train 侧响亮拒绝无目标配置）。
    policy_coef: float = 1.0

    #: 可训参数范围（P2-B，2026-11-08）：`all` = 历史 BC；`policy` = 只训
    #: move/fire/hold 三个头（trunk 与 value_head 冻结）；`value` = 只训 value 头
    #: （等价 `policy_coef=0`）。**旧课程缺键 ⇒ `all`（逐字节不变）**。
    train_heads: Literal["all", "policy", "value"] = "all"
    seed: int = 1234
    #: 每 N epoch 中途 checkpoint（{out}.ckpt.{epoch}）；0 = 关
    ckpt_every: int = 0
    #: v6 三头 class weight 的**强度**（B2，2026-11-05）：1 = 完全 inverse-frequency
    #: （历史行为），0 = 不加权，中间值 = `w ** exp`。标定判据 = 预测边缘 ≈ 标签边缘；
    #: v6 fire 分布 ~90:10:9 时满额加权会把正例预测率从 ~9.7% 推到 ~23.6%。
    #: val 侧只多记一条 `val_loss_weighted`，`val_loss` 仍保持未加权（与 majority 同尺度）。
    weight_exponent: float = 1.0
    #: fire 头 BCE 的正例权重（2026-09-14）。语料 fire 正例仅 ~7%（1:12.6），不加权的
    #: BCE 会让 fire 头退化成"几乎不发/乱发"：实测 fire_acc 0.770 < "永不发射"常数
    #: 基线 0.927。`auto` = 按训练集 neg/pos 自动定（≈12.6）；显式数 = 直接用；
    #: 0 = 关闭（旧行为，保持历史 checkpoint 语义）。
    fire_pos_weight: float | Literal["auto"] = "auto"
    #: **DEPRECATED（2026-11-05）**：新课程写顶层 `action_v6`；本键只为兼容 `bc-v6-train` 保留。
    #: v6 三头（move4/fire3/hold2）。语料来自 `export-rl-rollout.ts`
    #: （`a_move/a_fire/a_hold + mask.npy(9)`，不是 legacy 的 `actions/masks(7)`）。
    #: 2026-11-04：数据链已接通（`npyio._load_dataset_v6` + `dataset.NNDataset` 10 元组 +
    #: `train/bc.py` 三头 CE）。**开它必须配 `data_dir` 指向 v6 语料**，否则会把 legacy
    #: 语料按 v6 读（缺 `a_move.npy` 会响亮 FileNotFoundError，不会静默错配）。
    action_v6: bool = False
    #: 空间头（`policy_features` 吃 `hidden + POLICY_EXTRA_DIM`）。**z5/z7 的权重全是 True**
    #: （`arch.policyExtra=true`）⇒ BC 产物要与它们同构就必须开；关着训出来的权重
    #: `policy_features` 宽度不同，后续 PPO 的 `load_state_into` 会静默跳过那几层。
    policy_extra: bool = False
    #: warm-start 起点（权重 JSON 路径；绝对路径或仓库根相对；"" = 从随机起）。
    #: 发布时随 payload 以 init_weights.json 落 job 目录，worker 以它作 `--resume`
    #: 起点（优化器/LR 全新；value 头由宽容 loader 忽略）。进行中的续跑归续跑权重所有，
    #: 不重定起点。init_weights_fp 进幂等键：换起点 = 不同 job（防跨起点污染）。
    init_from: str = ""

    @field_validator("epochs")
    @classmethod
    def _epochs_ge1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("train.epochs 必须 ≥ 1")
        return v

    @field_validator("patience")
    @classmethod
    def _patience_ge0(cls, v: int) -> int:
        if v < 0:
            raise ValueError("train.patience 必须 ≥ 0")
        return v

    @field_validator("weight_exponent")
    @classmethod
    def _weight_exponent_ge0(cls, v: float) -> float:
        if v < 0:
            raise ValueError("train.weight_exponent 必须 ≥ 0")
        return v

    @field_validator("policy_coef")
    @classmethod
    def _policy_coef_ge0(cls, v: float) -> float:
        if v < 0:
            raise ValueError("train.policy_coef 必须 ≥ 0")
        return v

    @field_validator("lr")
    @classmethod
    def _lr_pos(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("train.lr 必须 > 0")
        return v


class BcCourseConfig(BaseModel, CustomStagesMixin):
    """BC 课程配置文件（`nn-training/curricula/<name>.bc.jsonc`）。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str = "unnamed-bc"
    #: 文件种类自述（与 .bc.jsonc 后缀双保险；编排器校验一致）
    kind: Literal["bc"] = "bc"
    #: 关卡引用（`nn-training/levels/<name>.jsonc` 或路径）；设置后 stages/difficulty/player
    #: 只能来自关卡；四类环境覆盖 + `max_ticks` 可在课程侧覆盖（§49，同 RL 课程）
    level: str = ""

    #: 动作空间 v6 三头（move4/fire3/hold2）的**唯一事实源**。
    #: None = 从已废弃的 `train.action_v6` 推导（向后兼容 `bc-v6-train`）；
    #: 顶层非 None 且 train 显式不同 ⇒ 加载时响亮报错（不静默覆盖）。
    #: 只有为 True 时才进 `bc_corpus_identity_fp`（legacy fp 逐字节不变）。
    action_v6: bool | None = None

    #: **控制台**读的起点（`dashboard/src/stack/courses.ts::resolveCourseBc` 只认这个顶层键，
    #: 缺它就回退 `tmp/ep60/battle2-p1bc/run/weights.json` 并报「初始权重缺失」）。
    #: Python 侧不消费它（真正的起点是 `train.init_from`），但必须允许存在——
    #: `extra="forbid"` 否则会把课程判成非法。**两处必须指同一文件**。
    bc: str = ""

    # ---- 环境（无 level 引用时内联；语义与 RL 课程同表） ----
    stages: str | list = "0-3"
    difficulty: str = "hard"
    max_ticks: int = 12000
    player: BcPlayerBlock = BcPlayerBlock()

    # ---- 语料 / 训练 / 评估 ----
    corpus: BcCorpusBlock = BcCorpusBlock()
    train: BcTrainBlock = BcTrainBlock()
    eval: BcEvalBlock = BcEvalBlock()

    # ---- 运行 ----
    #: BC 轮数（每轮：新语料（种子轮转）→ 云端 BC 训练 → 权重归档）
    iters: int = 1
    #: 跑完预定轮自动停课（删开课标记；hub 模式/暂停意图不动，自然收官不是用户停课）。
    #: 缺省关（RL 语义：收官不清标记，重开即续跑）。一次性的诊断/剂量腿开它。
    auto_stop: bool = False
    #: 语料派发并发（bc_dispatch 节点任务并发上限参考）
    workers: int = 8
    #: 权重落位路径（verify_and_land_bc 原子写；缺省 tmp/<stem>/weights.json）
    out: str = ""
    #: traj 目录（账本/remote-jobs/bc-data 根；缺省 tmp/<stem>）
    traj: str = ""
    #: 语料落盘根（缺省 <traj>/bc-data）
    data_dir: str = ""
    #: 归档（缺省 nn-training/weights/ + prefix=课程名）
    backup_dir: str = ""
    backup_prefix: str = ""

    # ------------------------------------------------------------ 派生

    @property
    def stage_ids(self) -> list[int]:
        """本课程采语的 stage ID 列表（真实关 0-based / 自定义关 2000+i）。"""
        if isinstance(self.stages, str):
            from biz.course import parse_range

            return parse_range(self.stages)
        from worker.config import CUSTOM_STAGE_BASE

        return [CUSTOM_STAGE_BASE + i for i in range(len(self.stages))]

    @property
    def resolved_action_v6(self) -> bool:
        """动作空间单源解析（None = 回退已废弃的 `train.action_v6`）。"""
        return bool(self.action_v6 if self.action_v6 is not None else self.train.action_v6)

    @property
    def label_policy_file(self) -> Path | None:
        """`label_policy` → 绝对路径；空串 = 未配置（legacy / 纯训练课）。"""
        raw = str(self.corpus.label_policy or "").strip()
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_absolute() else REPO_ROOT / raw

    @property
    def label_policy_fp(self) -> str:
        """标签权重文件字节 sha256（换权重 = 新语料）；未配置 = 空串。"""
        p = self.label_policy_file
        return "" if p is None else sha256_file(p)

    def resolve_corpus_dir(self, stem: str) -> Path:
        """fixed 的语料根；未配置时回退课程 `data_dir`（不 stat，缺失在加载处报）。"""
        raw = str(self.corpus.fixed_dir or "").strip()
        return Path(raw) if raw else Path(self.resolve_data_dir(stem))

    def stages_range(self) -> str:
        return ",".join(str(i) for i in self.stage_ids)

    def custom_stage_payload(self, stage_id: int) -> str | None:
        """stage_id → 下发远端的 stageJson 串（非自定义关返回 None）。

        与 CourseConfig.stage_json 同构（剥离注释的纯 JSON，≤4KB）。"""
        if not isinstance(self.stages, list):
            return None
        from worker.config import CUSTOM_STAGE_BASE, STAGE_JSON_MAX_BYTES

        i = stage_id - CUSTOM_STAGE_BASE
        if not (0 <= i < len(self.stages)):
            return None
        spec = self.stages[i]
        payload = {
            "name": spec.get("name", ""),
            "grid": spec.get("grid"),
            "forces": spec.get("forces", ""),
            "count": spec.get("count")
            if spec.get("count") is not None
            else len(spec.get("forces", "")),
        }
        if spec.get("player_spawn") is not None:
            payload["player_spawn"] = spec["player_spawn"]
        if spec.get("enemy_spawns"):
            payload["enemy_spawns"] = spec["enemy_spawns"]
        if spec.get("spawn_variants"):
            payload["spawn_variants"] = spec["spawn_variants"]
        s = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if len(s.encode("utf-8")) > STAGE_JSON_MAX_BYTES:
            raise ValueError(f"bc 课程 stageJson 超限（stage {stage_id}）")
        return s

    def resolve_out(self, stem: str) -> str:
        return self.out or f"tmp/{stem}/weights.json"

    def resolve_traj(self, stem: str) -> str:
        return self.traj or f"tmp/{stem}"

    def resolve_data_dir(self, stem: str) -> str:
        return self.data_dir or f"{self.resolve_traj(stem)}/bc-data"

    def resolve_backup_prefix(self, stem: str) -> str:
        return self.backup_prefix or self.name or stem

    @model_validator(mode="after")
    def _action_v6_single_source(self) -> BcCourseConfig:
        """顶层是唯一事实源；与显式 `train.action_v6` 冲突 ⇒ 响亮报错（不静默覆盖）。"""
        if (
            self.action_v6 is not None
            and "action_v6" in self.train.model_fields_set
            and bool(self.train.action_v6) != bool(self.action_v6)
        ):
            raise ValueError(
                "action_v6 冲突：顶层 action_v6="
                f"{self.action_v6} 与 train.action_v6={self.train.action_v6} 不一致；"
                "顶层是唯一事实源，请删掉 train.action_v6（已废弃）"
            )
        return self

    @model_validator(mode="after")
    def _bc_matches_init_from(self) -> BcCourseConfig:
        """顶层 `bc`（控制台）与 `train.init_from`（Python）不能指向两份权重。

        BC 课程允许 `bc` 留空（控制台不消费它；真正的起点是 `train.init_from`），所以只在
        两侧都写时比较。两边都写却指向不同文件 = 控制台播种的种子和训练 warm-start 不是同一份
        ——§384 事故的同构，必须在加载时响亮拒绝，而不是等到面板/训练各跑一份。
        """
        bc = str(self.bc or "").strip()
        init = str(self.train.init_from or "").strip()
        if not bc or not init:
            return self

        def _abs(raw: str) -> Path:
            p = Path(raw)
            return (p if p.is_absolute() else REPO_ROOT / p).resolve()

        bc_path, init_path = _abs(bc), _abs(init)
        if bc_path != init_path:
            raise ValueError(
                "bc 与 train.init_from 不一致："
                f"bc={bc!r}（{bc_path}） vs train.init_from={init!r}（{init_path}）；"
                "两者必须指向同一权重文件（BC 课可只写 train.init_from）"
            )
        return self

    @model_validator(mode="after")
    def _course_env_requires_stages_list(self) -> BcCourseConfig:
        """`eval.course_env` 只对 list 型 stages 生效（range 型没有逐关 stageJson）。

        range 型课程（`stages: "0-34"`）的 stage 号是真实关号，云端 `mode=eval` 会按
        `STAGES[stage]` 跑，课程的四类覆盖（enemies/lives/star）在关卡文件里根本没生效
        ⇒ 静默跑错面板。这里 fail-closed。
        """
        if self.eval.course_env and not isinstance(self.stages, list):
            raise ValueError(
                "eval.course_env=true 需要课程 stages 是 list（自定义关/挑关后）；"
                f"当前 stages={self.stages!r} 是范围串，云端会按真实关号跑、丢掉环境覆盖"
            )
        return self

    @model_validator(mode="after")
    def _v6_collection_requires_label_policy(self) -> BcCourseConfig:
        """v6 采集不能“随便拿个权重”：没有标签策略 = 没有可复现的标签来源。"""
        if (
            self.resolved_action_v6
            and int(self.corpus.games_per_stage) > 0
            and not str(self.corpus.label_policy or "").strip()
        ):
            raise ValueError(
                "action_v6 采集课程必须配置 corpus.label_policy（标签策略权重路径，"
                "仓库根相对或绝对）；纯训练课（games_per_stage: 0）可留空"
            )
        return self


def resolve_bc_course(name_or_path: str) -> Path:
    """`--course bc-c4` → `curricula/bc-c4.bc.jsonc`；存在路径原样。"""
    p = Path(name_or_path)
    if p.exists():
        return p
    cand = CURRICULA_DIR / f"{name_or_path}{BC_SUFFIX}"
    if not cand.exists():
        available = sorted(f.name for f in CURRICULA_DIR.glob(f"*{BC_SUFFIX}"))
        raise FileNotFoundError(
            f"BC 课程 '{name_or_path}' 不存在（查找 {cand}）；可用：{available}"
        )
    return cand


def is_bc_course(name_or_path: str) -> bool:
    """课程键是否为 BC 课程（排查用的一行判据，**唯一一份**）。

    与 `resolve_bc_course` 同一份事实：`curricula/<课>.bc.jsonc` 存在；控制台是同一口径
    （`dashboard/src/stack/courses.ts::isBcCourse`）。**不靠账本推断**——刚建的 BC 课程账本
    是空的，靠事件猜会把首轮判成 RL（症状：指针永远停在 it1）。
    """
    if not name_or_path:
        return False
    p = Path(name_or_path)
    if str(p).endswith(BC_SUFFIX):
        return p.exists()
    return (CURRICULA_DIR / f"{name_or_path}{BC_SUFFIX}").exists()


def load_bc_course(path_or_name: str | Path) -> BcCourseConfig:
    """读 JSONC BC 课程 → `BcCourseConfig`。

    level 注入 + 环境覆盖（`stage_select` / `enemies` / `lives` / `star` / `max_ticks`）与
    RL 课程走**同一份** `biz.course_resolve.apply_level_file`（§49）——两份实现会漂移，
    而「环境语义归关卡文件唯一持有」这条规则没有 BC 特例。
    """
    from common.jsonc import load as _load_jsonc

    p = resolve_bc_course(str(path_or_name))
    d = _load_jsonc(str(p))
    apply_level_file(d, str(d.get("name", path_or_name)))
    course = BcCourseConfig(**d)
    if course.kind != "bc":  # pragma: no cover — Literal 已挡，双保险
        raise ValueError(f"BC 课程 kind 必须是 'bc'（收到 {course.kind!r}）")
    return course


def bc_corpus_identity_fp(course: BcCourseConfig) -> str:
    """语料身份指纹（D14 语义版，BC 口径）：sha256(canonical(env + corpus 参数))。

    覆盖 = 决定「一个样本是什么」的全部字段：**obs 编码布局（schema major + 指纹）** /
    stages（解析后）/ difficulty / max_ticks / player /
    corpus{games_per_stage, wins_only, near_miss_times, seed_rotate}；
    新增 `source=fixed` / `outcome_policy=ratio` / `target` / 非默认 `scope` 仅在激活时加入
    （legacy 默认配置的 fp 逐字节不变）；**仅 v6 再加** action_v6 + label_mode。
    `fixed` 的 `fixed_dir`/语料字节**不进**课程指纹（数据身份另记 `data_fp`）。
    **刻意排除** train 超参 / iters / workers / out / traj / backup 等预算、优化器与路径键
    （与 biz.config.corpus_identity_fp 同分类学：这些改动不构成语料混入，不得触发
    云端 D14 拒收）。哈希**解析后**的值：关卡文件注释/格式变动不影响身份。

    ⚠ schema 必须在内（2026-09-13 补）：**agent 结果缓存键 = `iterId:mode:kind:stage:seed`
    （common/distribution.py:813），不含 codehash；而 BC 的 iterId = `bc-it{it}-{fp12}`（bc_dispatch.py:109）
    既不含课程名、也不含 runId**。所以 fp 里漏掉 schema 的后果是：v2→v3 的 MAJOR bump 之后
    重跑同一课程，fp 逐字不变（实测 bc-c4 / bc-c4-v3 均为 f60406b20e78…）⇒ 同一 iterId ⇒
    节点直接**回放旧 era 的 shard**，v3 编码器一次都跑不到。RL 侧因为 runId 每次启动重新生成
    （dispatch.py:34）而不受影响，BC 侧没有这层保护，只能靠身份指纹本身含 schema。
    """
    from common.schema import OBS_SCHEMA_MAJOR, SCHEMA_FINGERPRINT

    stages = list(course.stages) if isinstance(course.stages, list) else course.stages
    payload: dict[str, Any] = {
        "kind": "bc",
        # 编码布局：schema bump / 指纹变化 ⇒ 「一个样本是什么」已变，身份必须跟着变
        "obs_schema_major": OBS_SCHEMA_MAJOR,
        "obs_schema_fingerprint": SCHEMA_FINGERPRINT,
        "stages": stages,
        "difficulty": course.difficulty,
        "max_ticks": course.max_ticks,
        "player": course.player.model_dump(),
        "corpus": {
            "games_per_stage": course.corpus.games_per_stage,
            "wins_only": course.corpus.wins_only,
            "near_miss_times": course.corpus.near_miss_times,
            "seed_rotate": course.corpus.seed_rotate,
        },
    }
    if course.corpus.source != "collect":
        payload["corpus"]["source"] = course.corpus.source
    if course.corpus.outcome_policy != "all":
        payload["corpus"]["outcome_policy"] = course.corpus.outcome_policy
        if course.corpus.target is not None:
            payload["corpus"]["target"] = course.corpus.target.model_dump()
        if course.corpus.scope != "stage":
            payload["corpus"]["scope"] = course.corpus.scope
    if course.resolved_action_v6:
        # 仅 v6 进身份：legacy 课程 fp 逐字节不变。换动作空间/标签来源 = 新语料；
        # 否则 agent 缓存键（bc-it{it}-{corpus_fp[:12]}）会静默回放旧 shard。
        payload["action_v6"] = True
        payload["label_mode"] = course.corpus.label_mode
        # label_policy_fp 是权重内容身份：同一路径换文件也必须换 corpus_fp，否则
        # iterId（bc-it{it}-{fp12}）会命中旧标签权重产出的 shard 缓存。
        payload["label_policy_fp"] = course.label_policy_fp
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def round_seeds(course: BcCourseConfig, it: int) -> list[int]:
    """轮 it（1-based）的语料种子表：[1 + (it-1)*seed_rotate, ... + games_per_stage)。

    §15.1 语料轮转：轮 it 的 (stage, seed) 对不得与任何更早轮重复（seed_rotate ≥
    games_per_stage 时严格不重；< 时部分重叠由 data_fp 仍可区分轮次，编排器按轮
    分目录落盘）。"""
    n = int(course.corpus.games_per_stage)
    if n <= 0:
        return []
    start = 1 + (int(it) - 1) * int(course.corpus.seed_rotate)
    return list(range(start, start + n))

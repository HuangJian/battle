"""rl/bc_config.py — BC 课程配置（`curricula/<name>.bc.jsonc`）。

BC（行为克隆）课程独立于 RL 的 `CourseConfig`（plan/bc-cloud-integration.plan.md §2）：
mode 红线 / gates / ppo_schedule / 热加载字段全不适用，且 `CourseConfig.extra="forbid"`
会拒绝 BC 专有键。独立文件种类 `.bc.jsonc` 让控制台/编排器一眼分流。

与 CourseConfig 共享的语义：
  - JSONC 子集（`//` 行注释，rl/jsonc.py）；`extra="forbid"` 拼错即响亮报错；
  - `"level"` 关卡引用：stages/difficulty/max_ticks/player 四类环境键**只能**来自关卡
    文件（levels/*.jsonc），课程侧重复声明 = 配置冲突 raise（DECISIONS
    §2026-09-13-level-extraction 同规则）；
  - D14 语料身份：`bc_corpus_identity_fp` = 语料决定字段（env + corpus 参数）的解析值
    语义哈希；train 超参 / 预算 / 路径刻意排除——改它们不构成语料变化。

本模块 torch-free / numpy-free（run_bc.py 与单测在无 torch 环境导入）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CURRICULA_DIR = Path(__file__).resolve().parent.parent / "curricula"
LEVELS_DIR = Path(__file__).resolve().parent.parent / "levels"

BC_SUFFIX = ".bc.jsonc"

#: level 引用持有后、课程侧禁止重复声明的环境键（与 rl/config._LEVEL_ENV_KEYS 同表）
_BC_LEVEL_ENV_KEYS = ("stages", "difficulty", "max_ticks", "player")


class BcPlayerBlock(BaseModel):
    """语料采集的玩家口径（命数/星级覆盖 → 导出器 livesOverride/playerLevel）。"""

    model_config = ConfigDict(extra="forbid")

    lives: int | None = None
    level: int | None = None


class BcCorpusBlock(BaseModel):
    """God-AI 教师语料生成参数（→ export-godai-bc / rl/bc_dispatch）。"""

    model_config = ConfigDict(extra="forbid")

    #: 每关局数（轮 r 的种子 = [1 + (r-1)*seed_rotate, ... + games_per_stage)，§15.1 轮转）
    games_per_stage: int = 40
    #: wins-only（历史口径：仅保留 stage_clear 局的 shard；败局 = 合法"跳过"结果）
    wins_only: bool = True
    #: 守家帧超采样倍数（export-godai-labels 同款；0/1 = 关）
    near_miss_times: int = 3
    #: 语料轮转步长（轮 r 种子起点 = 1 + (r-1)*seed_rotate；0 = 每轮同种子，仅单轮课用）
    seed_rotate: int = 0

    @field_validator("games_per_stage", "near_miss_times", "seed_rotate")
    @classmethod
    def _nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("corpus 计数类字段必须 ≥ 0")
        return v


class BcTrainBlock(BaseModel):
    """BC 训练超参（→ 云端 train/bc.py train() 的 SimpleNamespace 参数）。"""

    model_config = ConfigDict(extra="forbid")

    #: 'bc' = NNPolicy（双头），'student' = CoordConv-ConvMixer-Lite（蒸馏主线）
    arch: Literal["bc", "student"] = "student"
    epochs: int = 60
    batch: int = 512
    lr: float = 3e-3
    #: mirrorX 在线增强概率（训练 split）
    mirror_p: float = 0.5
    #: shard 级 train/val 切分比例
    val_split: float = 0.1
    #: >0 且语料带 returns.npy 时对 value 头做 MC 回归（历史教训：默认 0——
    #: value MC 回归在 coef 0.05 下都爆，docs/nn.progress.md §15 M3）
    value_coef: float = 0.0
    seed: int = 1234
    #: 每 N epoch 中途 checkpoint（{out}.ckpt.{epoch}）；0 = 关
    ckpt_every: int = 0

    @field_validator("epochs")
    @classmethod
    def _epochs_ge1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("train.epochs 必须 ≥ 1")
        return v

    @field_validator("lr")
    @classmethod
    def _lr_pos(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("train.lr 必须 > 0")
        return v


class BcCourseConfig(BaseModel):
    """BC 课程配置文件（`nn-training/curricula/<name>.bc.jsonc`）。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str = "unnamed-bc"
    #: 文件种类自述（与 .bc.jsonc 后缀双保险；编排器校验一致）
    kind: Literal["bc"] = "bc"
    #: 关卡引用（`nn-training/levels/<name>.jsonc` 或路径）；设置后四类环境键只能来自关卡
    level: str = ""

    # ---- 环境（无 level 引用时内联；语义与 RL 课程同表） ----
    stages: str | list = "0-3"
    difficulty: str = "hard"
    max_ticks: int = 12000
    player: BcPlayerBlock = BcPlayerBlock()

    # ---- 语料 / 训练 ----
    corpus: BcCorpusBlock = BcCorpusBlock()
    train: BcTrainBlock = BcTrainBlock()

    # ---- 运行 ----
    #: BC 轮数（每轮：新语料（种子轮转）→ 云端 BC 训练 → 权重归档）
    iters: int = 1
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
    def is_custom_stages(self) -> bool:
        return isinstance(self.stages, list)

    @property
    def stage_ids(self) -> list[int]:
        """本课程采语的 stage ID 列表（真实关 0-based / 自定义关 2000+i）。"""
        if isinstance(self.stages, str):
            from rl.course import parse_range

            return parse_range(self.stages)
        from rl.config import CUSTOM_STAGE_BASE

        return [CUSTOM_STAGE_BASE + i for i in range(len(self.stages))]

    def stages_range(self) -> str:
        return ",".join(str(i) for i in self.stage_ids)

    def custom_stage_payload(self, stage_id: int) -> str | None:
        """stage_id → 下发远端的 stageJson 串（非自定义关返回 None）。

        与 CourseConfig.stage_json 同构（剥离注释的纯 JSON，≤4KB）。"""
        if not isinstance(self.stages, list):
            return None
        from rl.config import CUSTOM_STAGE_BASE, STAGE_JSON_MAX_BYTES

        i = stage_id - CUSTOM_STAGE_BASE
        if not (0 <= i < len(self.stages)):
            return None
        spec = self.stages[i]
        payload = {
            "name": spec.get("name", ""),
            "grid": spec.get("grid"),
            "forces": spec.get("forces", ""),
            "count": spec.get("count") if spec.get("count") is not None else len(spec.get("forces", "")),
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


def load_bc_course(path_or_name: str | Path) -> BcCourseConfig:
    """读 JSONC BC 课程 → `BcCourseConfig`（level 注入规则与 RL 课程同表）。"""
    from rl.jsonc import load as _load_jsonc

    p = resolve_bc_course(str(path_or_name))
    d = _load_jsonc(str(p))
    if d.get("level"):
        from rl.config import resolve_level

        lvl = _load_jsonc(str(resolve_level(str(d["level"]))))
        for k in _BC_LEVEL_ENV_KEYS:
            if k in d:
                raise ValueError(
                    f"BC 课程 '{d.get('name', path_or_name)}' 引用 level='{d['level']}' 后不得再声明 "
                    f"`{k}`（环境语义归关卡文件唯一持有）"
                )
            if k in lvl:
                d[k] = lvl[k]
    course = BcCourseConfig(**d)
    if course.kind != "bc":  # pragma: no cover — Literal 已挡，双保险
        raise ValueError(f"BC 课程 kind 必须是 'bc'（收到 {course.kind!r}）")
    return course


def bc_corpus_identity_fp(course: BcCourseConfig) -> str:
    """语料身份指纹（D14 语义版，BC 口径）：sha256(canonical(env + corpus 参数))。

    覆盖 = 决定「一个样本是什么」的全部字段：stages（解析后）/ difficulty / max_ticks /
    player / corpus{games_per_stage, wins_only, near_miss_times, seed_rotate}。
    **刻意排除** train 超参 / iters / workers / out / traj / backup 等预算、优化器与路径键
    （与 rl.config.corpus_identity_fp 同分类学：这些改动不构成语料混入，不得触发
    云端 D14 拒收）。哈希**解析后**的值：关卡文件注释/格式变动不影响身份。
    """
    stages = (
        list(course.stages) if isinstance(course.stages, list) else course.stages
    )
    payload = {
        "kind": "bc",
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

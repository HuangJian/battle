"""remote/publish_spec.py — 一轮远程 job 两端的**全部数据形状**（发布 → 校验落位）。

从 `remote/hub_client.py` 拆出（LOC 预算 + 单一所有者）：线协议只认这些对象，
`publish_job(spec)` 之后的打包 / manifest 只读它的字段。调用方直接构造本对象，
`publish_job` 不再接受字段 kwargs。

发布端：`PublishSpec`（唯一入参）与发布流程的**两个中间形状**（2026-11-18，长函数存量第三十三刀）
—— `PublishFacts`（第一相算出的指纹 / 内容寻址字节）与 `PublishStaged`（第二相建好的
manifest + job 目录）。发布动作（`_publish_facts` / `_publish_stage` / `_publish_commit`）
住 `remote/hub_publish.py`。

落位端（2026-11-23，P0-3 批 K）：`LandSpec` / `BcLandSpec` —— `verify_and_land` /
`verify_and_land_bc` 的入参。两个函数的 `result` 与 `manifest` 是两个**同型 dict**、
却排在位置口上（互换不抛，三重校验全部失配后报「云端跑错了 job」）；两份字段表也不同
（PPO 有 init-weights fencing 与 `mem`，BC 有 `round_name` / `data_root` / `fixed_source`）
⇒ 不共用一份 spec。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class PublishSpec:
    """一次发布的**全部输入**（签名曾直接摊成 56 个关键字参数）。

    线协议只认这个对象：`publish_job(spec)` 之后的打包 / manifest 只读它的字段。
    新增字段只改这里 + `publish_job` 的绑定块。
    """

    job_root: str | Path
    jsonl_path: str | Path
    run_id: str
    it: int
    traj_dir: str | Path
    shard_dirs: list[Path]
    commit: str
    code_sha256: str
    course: str
    course_fp: str
    mode: str
    epochs: int
    mb: int
    lr: float
    code_zip_path: str | Path | None = None
    course_name: str = ""
    corpus_fp: str = ""
    init_weights_path: str = ""
    ckpt_remote_dir: str | Path | None = None
    reward_formula: str = ""
    formula_hash: str = ""
    metrics_version: int = 0
    gamma: float = 0.0
    lam: float = 0.0
    kl_coef: float = 0.0
    kl_cap: float | None = None
    adv_norm: str = "auto"
    normalize_ret: bool = False
    kickstart_kl: float = 0.0
    ent_coef: float | None = None
    ref_weights_b64: str = ""
    ref_weights_fp: str = ""
    wire_diet: bool = False
    reward_spec: object | None = None
    presegmented: Mapping[str, bytes] | None = None
    # `object` 而非 `ShardSlim`：`remote_dag` 把 `TYPE_CHECKING` 体当顶层边，而
    # `payload_slim` 是 L0 ⇒ 本模块（同样 L0）不能指它；消费端 `publish_job` 负责 cast。
    diet_slim: object | None = None
    mem: Mapping[str, dict] | None = None
    demo_bank_bytes: bytes | None = None
    demo_bc_coef: float = 0.0
    demo_per_mb: int = 0
    value_anchor: float = 0.0
    shuffle: bool = True
    schedule_raw: list | None = None
    per_stage_quota: int = 0
    slim: bool = False
    keep_init_weights: bool = False
    kind: str = "ppo"
    extra: dict | None = None
    rollout_spec: dict | None = None
    ts_code_sha256: str = ""
    ts_code_zip_path: str | Path | None = None
    #: P2（plan/replay-corpus.plan.md）：语料运输格式 + trainer 预构建的转换器 bundle。
    #: replay 时必须给 converter（随 payload 附件下发，worker 只跑不建）；
    #: full 时两者都省略 ⇒ manifest/payload 逐字节旧行为。
    corpus_fmt: str = "full"
    replay_converter_path: str | Path | None = None
    plan_bytes: bytes | None = None
    register: bool = True
    dispatch: str = ""
    role: str = ""


@dataclass(frozen=True, kw_only=True)
class LandSpec:
    """`verify_and_land`（PPO 回传）的唯一入参。

    `result` 与 `manifest` 是两个相邻的 `dict`：互换不抛，只会让三重校验全部失配 ——
    现场看到的是一条「云端跑的不是这个 job」的响亮拒绝，而真正错的是本机传参。
    """

    #: 云机回传的结果（`weights_json` 在形态位命中时是 delta 容器，落位前先读侧落地）。
    result: dict
    #: 发布那一刻写进 payload 的 manifest（`data_fp` / `commit` 就是这里的值）。
    manifest: dict
    #: 本机 `args.out`：既是 fencing 基准（`init_weights_fp`），也是落盘目标。
    init_weights_path: str
    traj_dir: str | Path
    it: int
    out_weights: str
    #: 活目录对账是否按 state_init 口径过滤（与发布端同一条）。
    state_init: bool = False
    #: 零落盘：发布那一刻的内存账快照（回传 shard 也要算进活目录）。
    mem: Mapping[str, dict] | None = None
    log: Callable[[str], None] | None = None


@dataclass(frozen=True, kw_only=True)
class BcLandSpec:
    """`verify_and_land_bc`（BC 回传）的唯一入参。

    与 `LandSpec` 同名不同形：BC 无 warm-start ⇒ 没有 init-weights fencing、没有 `mem`，
    换来 `round_name`（报错要点名哪一轮）/ `data_root`（fixed 课的扫描根）/ `fixed_source`
    （按 `*_s*_seed*` 认 shard、跳过 D14）。共用一份 spec 会同时长出三个「PPO 用不着」的字段。
    """

    result: dict
    manifest: dict
    traj_dir: str | Path
    it: int
    out_weights: str
    #: 报错行点名的轮名（空 = 用 `it` 拼）。
    round_name: str = ""
    #: fixed 课的 bc shard 扫描根（`fixed_source=True` 时才读）。
    data_root: str | Path | None = None
    #: 扫 `data_root`、按 `*_s*_seed*` 认 shard 并跳过 D14（与发布端同口径）。
    fixed_source: bool = False
    log: Callable[[str], None] | None = None


@dataclass(frozen=True)
class PublishFacts:
    """发布第一相（校验 + 指纹 + 内容寻址字节）；后两相只读它。"""

    kind: str
    schedule_raw: list
    rollout_spec: dict | None
    fp: str
    init_weights_fp: str
    opt_raw: bytes
    opt_sha: str
    use_opt_blob: bool
    ref_raw: bytes
    use_ref_blob: bool
    demo_raw: bytes
    demo_sha: str
    use_demo_blob: bool
    init_raw: bytes
    use_init_blob: bool
    opt_init: str


@dataclass(frozen=True)
class PublishStaged:
    """发布第二相（manifest + job 身份 + payload 附带文件）；第三相只读它。"""

    m: dict
    jid: str
    jd: Path
    tmp_extra_dir: Path
    extra_files: list[Path]

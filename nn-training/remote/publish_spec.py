"""remote/publish_spec.py — 一次发布的**全部输入**（`publish_job` 的唯一入参对象）。

从 `remote/hub_client.py` 拆出（LOC 预算 + 单一所有者）：线协议只认这个对象，
`publish_job(spec)` 之后的打包 / manifest 只读它的字段。调用方直接构造本对象，
`publish_job` 不再接受字段 kwargs。
"""

from __future__ import annotations

from collections.abc import Mapping
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
    plan_bytes: bytes | None = None
    register: bool = True
    dispatch: str = ""
    role: str = ""

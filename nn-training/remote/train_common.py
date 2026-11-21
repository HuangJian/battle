"""remote/train_common.py —— 训练核的**框架无关半场**（2026-10-27，Paddle 双生落地时拆出）。

两条训练核（`train_core.run_training_core` 的 torch 版与 `_run_training_core_paddle`）之间有一段
**与框架完全无关**的判据与装配：

* `effective_epochs` / `kick_kl_value` / `blob_wanted` / `ppo_shards_root` —— 纯 manifest /
  `iter_info` 判据（可单测；无 torch、无 numpy、不联网）；
* `course_prep` —— 课程快照 → CourseConfig → reward_fn → 上下文更新。两条核必须在**同一处**
  求值：`formula_hash` 校验一旦分叉，混跑同一门课就会出现「同一份 manifest，一边收一边拒」；
* `decode_demo_bank` / `check_demo_bank` —— demo bank 的字节解码与字段校验（安全阀在
  `train_core._fetch_demo` 取得字节之后）。

**为什么单独成家**：Paddle 双生把这些函数从「只有一条调用链」变成「两条各调一次」，而
`remote/train_core.py` 已顶到 LOC 预算（`tests/test_python_loc_budget.py`）。放在这里还顺手钉住
「两核共用同一份判据」——哪天只改一边，混跑就开始分叉。

层号 **L3**（`tests/helpers/remote_dag.py`）：顶层只碰 `common.*`(L0) 与 `remote.wire`(L1)；
`worker.config`(L1) 与 `biz.reward_*` 都在**函数内**延迟 import；`load_ppo_episodes` 延迟
import `worker.ppo.lazy_corpus`(L2) ⇒ 秩 = 1 + 2 = **L3**。`train_core`(L4) 站在本模块上面。

2026-11-16（P3）：PPO 收尾账（`_ppo_done_log` / `_agg_block` + lazy chunks 的步数/装载器
读数）从 `train_core` 搬来这里——同一类「两条核共用的账」判据，也让训练核回到 LOC 预算内。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from common.logutil import secs
from common.protocol import (
    BLOB_OPT,
    BLOB_REF,
    ProtocolError,
    coef_active,
    encode_weights_json,
    pack_result_v2,
)
from remote.wire import _wire_block
from worker.ppo.demo import DEMO_BANK_FIELDS, DemoBank
from worker.ppo.rng import PPORng


def effective_epochs(manifest_epochs: int, epoch_mult: float) -> int:
    """本轮真正要跑的 PPO 遍数（纯函数）：`ceil(课程 epochs × N)`、最少 1；1.0 = 原样。

    只放大**更新遍数**：采样量/评估语料/关卡/命数都不动（与 `remote/plan_run` 的
    离线口径同一条）。`manifest_epochs <= 0` 时原样返回 0 —— 放大不制造训练量。
    """
    base = int(manifest_epochs or 0)
    # 0/缺省 = 这单本来就不训（非 PPO 腿）⇒ 放大不许凭空造出 1 遍。
    if base <= 0 or epoch_mult == 1.0:
        return base
    return max(1, math.ceil(base * epoch_mult))


def kick_kl_value(manifest: dict) -> float:
    """`kickstart_kl` 的**有效值**（纯函数）：低于 `coef_active` 阈值一律按 0（= 关）处理。

    抽出来的理由：B1 要把 opt/ref 合成一次 GET，就必须**在批量之前**知道 ref 要不要 ——
    而这个判据是纯 manifest 读（不依赖任何计算）。抽成函数后判据可单测，不用去源码里
    找字符串（那类判据改个变量名就红，而行为没变）。
    """
    kl = float(manifest.get("kickstart_kl", 0.0) or 0.0)
    if kl != 0.0 and not coef_active(kl):
        return 0.0
    return kl


def blob_wanted(
    manifest: dict, *, kick_kl: float, skip: frozenset[str] = frozenset()
) -> dict[str, str]:
    """本轮**要向 hub 要哪几件** blob（纯函数）→ `{name: sha}`。

    * `opt` —— 只要有 sha 就必进批（每轮都有的那一件）；
    * `ref` —— **只在 kick 打开时**进批（关了就不该为它付一次取字节的代价）；
    * sha 为空的名字**不进本批**（那走 `_resolve_blob` 的 inline/legacy 路径）。
    * `skip` —— 调用方已经**本机**拿到、确定不需要下行的名字（今天只有 opt：身份命中）。
      它是纯参数（不读环境/不写盘），所以「命中就不下」可以单测；`ref` 不在 skip 里——
      kick 打开时它是另一件字节。
    """
    wanted: dict[str, str] = {}
    opt_sha = str(manifest.get("opt_sha", "") or "")
    if opt_sha and BLOB_OPT not in skip:
        wanted[BLOB_OPT] = opt_sha
    if float(kick_kl) > 0.0:
        ref_sha = str(manifest.get("ref_sha", "") or "")
        if ref_sha:
            wanted[BLOB_REF] = ref_sha
    return wanted


def copy_iter_metrics(result: dict, iter_info: dict | None) -> None:
    """节点自己跑的 rollout 的采集口径 → 结果行（M3；两条后端腿共用一份）。

    `report` / `volume_stage_stats` / `greedy_quota_stats` 三个键不搬这一手就随
    `iter_info` 一起丢掉（老产物行没有这些键 = 留空，不编 0）。
    """
    if iter_info is None:
        return
    for key in ("report", "volume_stage_stats", "greedy_quota_stats"):
        if key in iter_info:
            result[key] = iter_info[key]


def load_ppo_episodes(shards_root: str, manifest: dict, prep_b: Any) -> Any:
    """PPO 语料装载 —— `ppo_loader=eager|lazy` 的**唯一分派点**（两条训练核共用）。

    eager 逐字节走旧 `np_core.load_episodes`；lazy 走 obs mmap + 逐 minibatch 物化。
    协议缺省仍是 eager（旧 manifest 不带键），lazy 始终由发布端显式声明。
    """
    from worker.ppo.lazy_corpus import PPOCorpusSpec, load_ppo_corpus

    return load_ppo_corpus(
        PPOCorpusSpec(
            shards_root,
            loader=str(manifest.get("ppo_loader", "eager") or "eager"),
            gamma=float(manifest["gamma"]),
            lam=float(manifest["lam"]),
            normalize_adv=str(manifest["adv_norm"]) != "none",
            normalize_ret=bool(manifest.get("normalize_ret", False)),
            per_stage_quota=int(manifest.get("per_stage_quota", 0) or 0),
            mb=int(manifest["mb"]),
            bundle=prep_b,
        )
    )


def ppo_shards_root(job_dir: str | Path, iter_info: dict | None) -> str:
    """PPO 的**装载根**（`plan/rollout-topup-fastpath` §3.5 / 评审 P0-1）。

    产出方（`worker.iter_rollout`）显式给出它：换根档下 rollout 的 shard 住在节点本地盘的
    轮 scratch 目录里（**不再搬回** job 目录），与本轮所有批的产出同在一个轮目录下
    （`topup{k}/` 镜像在里面）⇒ 递归扫一次就全拿。

    回落 `job_dir` 的两个面：① 回退档（`shards_root` 就是 job 目录，逐字老行为）；
    ② 老调用方/老 hub（`iter_info is None` 或没这个键）——不吃「空串键」当根。
    """
    return str((iter_info or {}).get("shards_root") or job_dir)


def course_prep(manifest: dict, job_dir: Path, epoch_mult: float, prep_b: Any) -> tuple[Any, int]:
    """课程上下文 + 本轮有效遍数 —— **两条训练核共用**（与框架无关的那一半）。

    落 `course.jsonc` → `load_course` → reward_fn → 上下文更新。两条核各写一份时最容易只改
    一边：`formula_hash` 校验一旦分叉，混跑同一门课就会出现「同一份 manifest，一边收一边拒」。
    """
    t0 = time.time()
    course_path = job_dir / "course.jsonc"
    course_path.write_text(manifest["course"], encoding="utf-8")
    from worker.config import load_course

    course = load_course(str(course_path))
    if course.reward_spec().identity() != manifest["formula_hash"]:
        raise ProtocolError(
            f"course formula_hash 与快照不符：manifest={manifest['formula_hash']} "
            f"本地算={course.reward_spec().identity()}"
        )
    from biz.reward_context import update as ctx_update
    from biz.reward_library import build_reward_fn

    ctx_update(
        reward_fn=build_reward_fn(course.reward_spec()),
        gamma=float(manifest["gamma"]),
        lam=float(manifest["lam"]),
        it=int(manifest["it"]),
        metrics_version=int(manifest["metrics_version"]),
        identity={"course": course.name, "formula_hash": manifest["formula_hash"]},
    )
    prep_b.add("课程", f"{secs(time.time() - t0)}s（load_course+reward）")
    # ★ 2026-10-26（用户口径）：训练侧放大只改 PPO **更新遍数**；manifest 本身**不重写**
    #   （hub 对账/产物指纹都绑在它上面），缺省 1.0 时不打任何新日志。
    epochs = effective_epochs(int(manifest.get("epochs") or 0), epoch_mult)
    if epoch_mult != 1.0:
        prep_b.add(
            "训练放大",
            f"PPO epochs ×{epoch_mult:g} ⇒ {epochs}（manifest {int(manifest.get('epochs') or 0)}）",
        )
    return course, epochs


def decode_demo_bank(raw: bytes) -> DemoBank:
    """demo bank 的 `.npz` 字节 → `DemoBank`（numpy 只在函数体内 import）。

    字段名对不对由 `check_demo_bank` 的安全阀判（这里只解码；`.npz` 的键是运行期才知道的，
    所以返回处是 `cast` 而不是逐个取键 —— 取键会把「缺字段」变成 `KeyError`，而那种情况要
    走安全阀那句「缺字段 X —— 拒收」）。
    """
    import io

    import numpy as np

    return cast(DemoBank, {k: np.asarray(v) for k, v in dict(np.load(io.BytesIO(raw))).items()})


def check_demo_bank(
    jid: str, bank: DemoBank, coef: float, per_mb: int, src: str, prep_b: Any
) -> None:
    """demo bank 的字段安全阀 + 一行装载读数（缺字段 = 拒收，不静默当纯 PPO 跑）。"""
    need = DEMO_BANK_FIELDS
    if not need.issubset(bank.keys()):
        raise ProtocolError(f"job {jid}: demo bank 缺字段 {sorted(need - set(bank.keys()))}——拒收")
    prep_b.add(
        "demo bank",
        f"N={bank['obs'].shape[0]} coef={coef:g} per_mb={per_mb} src={src}",
    )


def _agg_block(agg: dict, chunks: Any, total_steps: int) -> dict:
    """进 `result.agg` 的读数——两条核同一份字段（hub 侧按 backend 无关的同一形状读）。

    value/policy 的 `*_new_rms` 是门控遥测（x26-obs it90 / x27 冻结判决）：旧 worker 无此键
    ⇒ 0.0，additive；`steps` 是 mb 对齐后**实际训练**的步数（池子总量另有 `steps_pooled`）。
    """
    from worker.ppo.trainer import chunk_count, chunk_rows

    return {
        "policy": float(agg.get("policy", 0.0)),
        "value": float(agg.get("value", 0.0)),
        "entropy": float(agg.get("entropy", 0.0)),
        "kl": float(agg.get("kl", 0.0)),
        "per_step_kl": float(agg.get("per_step_kl", agg.get("kl", 0.0)) or 0.0),
        "clip_fraction": float(agg.get("clip_fraction", 0.0) or 0.0),
        # D8 轮内中止阈值是否触发（缺省关闭 ⇒ False；旧 worker 无键 ⇒ False）。
        "early_stopped": bool(agg.get("early_stopped", False)),
        "kickstart": float(agg.get("kickstart", 0.0)),
        "demo_bc": float(agg.get("demo_bc", 0.0)),
        "mean_ret": float(agg.get("mean_ret", 0.0)),
        "value_new_rms": float(agg.get("value_new_rms", 0.0) or 0.0),
        "policy_new_rms": float(agg.get("policy_new_rms", 0.0) or 0.0),
        "steps": chunk_rows(chunks),
        "steps_pooled": int(total_steps),
        "chunks": chunk_count(chunks),
    }


@dataclass
class _CoreRun:
    """一次训练核的派生状态；输入仍住 `TrainSpec`。"""

    base_url: str
    token: str
    spec: Any
    prep_b: Any = None
    course: Any = None
    epochs_eff: int = 0
    model: Any = None
    opt: Any = None
    device_t: Any = None
    use_dp: bool = False
    opt_raw: bytes = b""
    opt_dir: Path = field(default_factory=Path)
    opt_restore_sec: float = 0.0
    opt_src: str = ""
    steps_init: int = 0
    steps_box: dict[str, int] = field(default_factory=dict)
    weights_src: str = ""
    blob_hits: int = 0
    blob_miss_bytes: int = 0
    kick_kl: float = 0.0
    batch: dict = field(default_factory=dict)
    wanted: dict = field(default_factory=dict)
    ref_model: Any = None
    demo_coef: float = 0.0
    demo_per_mb: int = 0
    demo_bank: DemoBank | None = None
    shards_root: str = ""
    #: §P1-7 的三条显式随机流：两腿在算 per-job 种子那一行派生（`PPORng.from_seed`），
    #: 切块重排与 PPO 循环都从它取 —— 全局 `np.random` 不再进训练序列。缺省 = 种子 0，
    #: 只为工具/测试手工构造的 `_CoreRun`（生产两条腿在训练前一定会覆盖它）。
    rng: PPORng = field(default_factory=lambda: PPORng.from_seed(0))
    t_ppo: float = 0.0
    train_b: Any = None
    chunks: list = field(default_factory=list)
    total_steps: int = 0
    agg: dict = field(default_factory=dict)
    ppo_sec: float = 0.0
    wj_raw: bytes = b""
    wj_sent_raw: bytes = b""
    opt_tar_raw: bytes = b""
    opt_sent_raw: bytes = b""
    opt_tar_b64: str = ""
    t_art: float = 0.0


def _load_ppo_data(st: _CoreRun, engine) -> None:
    """读 shard 并分块（torch/paddle 两条核共用同一组引擎契约）。"""
    spec = st.spec
    manifest = spec.manifest
    prep_b = st.prep_b
    # P3（plan/replay-corpus.plan.md D6）：eager/lazy 的**唯一分派点**是
    # `load_ppo_episodes`（两条训练核共用）——这里不再各写一份是否 lazy 的判断。
    episodes = load_ppo_episodes(st.shards_root, manifest, prep_b)
    prep_b.emit(f"job {spec.jid}: 准备完成 {time.time() - spec.t_prep:.1f}s")
    from worker.ppo.trainer import corpus_samples

    st.total_steps = corpus_samples(episodes)
    # `st.rng` 一定在位（两腿在算 per-job 种子时派生；手工构造的用字段缺省=种子 0）。
    st.chunks = engine.chunk_episodes(
        episodes, int(manifest["mb"]), shuffle=bool(manifest["shuffle"]), rng=st.rng.chunk
    )


def _core_result(st: _CoreRun) -> tuple[dict, Any]:
    """两条核共用的 result / wire 形状（字节已在产物段准备好）。"""
    spec = st.spec
    jid, manifest, iter_info = spec.jid, spec.manifest, spec.iter_info
    agg, chunks, total_steps, ppo_sec = st.agg, st.chunks, st.total_steps, st.ppo_sec
    wj_sent_raw, opt_tar_b64 = st.wj_sent_raw, st.opt_tar_b64
    course, opt_restore_sec = st.course, st.opt_restore_sec
    blob_hits, blob_miss_bytes = st.blob_hits, st.blob_miss_bytes
    weights_src = st.weights_src
    payload_bytes, payload_dl_sec = spec.payload_bytes, spec.payload_dl_sec
    unpack_sec, prune_dirs = spec.unpack_sec, spec.prune_dirs
    weights_wire_bytes = spec.weights_wire_bytes
    ts_code_bytes, ts_code_hit = spec.ts_code_bytes, spec.ts_code_hit
    _t_art = st.t_art
    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        # 回传的 weights_json：旧形态用的是被缓存的那份字节（同字节 ⇒ 同 sha，别中途再读一次盘）；
        # R1 形态位命中时这里是 delta 容器（本机那份全量仍是 `wj_raw`，见上面产物段）。
        "weights_json": encode_weights_json(wj_sent_raw),
        "opt_tar_b64": opt_tar_b64,
        "agg": _agg_block(agg, chunks, total_steps),
        "commit_echo": manifest["commit"],
        "ppo_sec": ppo_sec,
    }
    # M3：节点自己跑的 rollout 的采集口径（两条后端腿共用一份；见 `train_common.copy_iter_metrics`）。
    copy_iter_metrics(result, iter_info)
    result["wire"] = _wire_block(
        payload_bytes=payload_bytes,
        payload_dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
        prune_dirs=prune_dirs,
        opt_restore_sec=opt_restore_sec,
        grad_sec=ppo_sec,
        # 产物段（save/pack/identity）自己的账：它不在 `ppo=` 里，是云机轮墙钟最大的一块暗区。
        art_sec=time.time() - _t_art,
        blob_hits=blob_hits,
        blob_miss_bytes=blob_miss_bytes,
        weights_src=weights_src,
        weights_bytes=weights_wire_bytes,
        ts_code_bytes=ts_code_bytes,
        ts_code_hit=ts_code_hit,
        rollout_sec=(iter_info or {}).get("rollout_sec"),
        bun_version=(iter_info or {}).get("bun_version"),
        # D10（plan/replay-corpus.plan.md）：语料格式 + replay pre-pass 的墙钟/字节账。
        corpus_fmt=str(manifest.get("corpus_fmt") or "full"),
        replay_bytes=(st.spec.replay_info or {}).get("replay_bytes"),
        labels_bytes=(st.spec.replay_info or {}).get("labels_bytes"),
        convert_sec=(st.spec.replay_info or {}).get("sec"),
        convert_workers=(st.spec.replay_info or {}).get("convert_workers"),
        convert_jobs=(st.spec.replay_info or {}).get("convert_jobs"),
        convert_fallback=(st.spec.replay_info or {}).get("convert_fallback"),
    )
    # 两遍收敛（同 echo 路径）：result_bytes 与自身体长自指，一遍差它的十进制位数。
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    return result, course

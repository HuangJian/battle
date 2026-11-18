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
from pathlib import Path
from typing import Any

from common.log_bundle import LogBundle
from common.logutil import secs
from common.protocol import BLOB_OPT, BLOB_REF, ProtocolError, coef_active
from remote.wire import _wire_time, note_ppo_tail


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
    from worker.ppo.lazy_corpus import load_ppo_corpus

    return load_ppo_corpus(
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
            f"PPO epochs ×{epoch_mult:g} ⇒ {epochs}"
            f"（manifest {int(manifest.get('epochs') or 0)}）",
        )
    return course, epochs


def decode_demo_bank(raw: bytes) -> dict:
    """demo bank 的 `.npz` 字节 → `{name: ndarray}`（numpy 只在函数体内 import）。"""
    import io

    import numpy as np

    return {k: np.asarray(v) for k, v in dict(np.load(io.BytesIO(raw))).items()}


def check_demo_bank(
    jid: str, bank: dict, coef: float, per_mb: int, src: str, prep_b: Any
) -> None:
    """demo bank 的字段安全阀 + 一行装载读数（缺字段 = 拒收，不静默当纯 PPO 跑）。"""
    need = {"obs", "scalars", "extra", "actions", "masks"}
    if not need.issubset(bank.keys()):
        raise ProtocolError(f"job {jid}: demo bank 缺字段 {sorted(need - set(bank.keys()))}——拒收")
    prep_b.add(
        "demo bank",
        f"N={bank['obs'].shape[0]} coef={coef:g} per_mb={per_mb} src={src}",
    )


def _ppo_done_log(
    jid: str, t_ppo: float, train_b: LogBundle, chunks: Any, total_steps: int, agg: dict
) -> float:
    """PPO 收尾读数（两条核同一组账、同一行措辞）→ 返回 `ppo_sec`（产物账要用）。

    不再单列 `job … PPO done in …` 一行：steps/chunks/kl + engine 折叠读数并入下游 wire
    总账行（`wire._wire_flush` 读取 `_WIRE[jid]["ppo_line"]` 拼到行尾，2026-10-28）。
    """
    from worker.ppo.trainer import chunk_count, chunk_rows, loader_desc

    ppo_sec = round(time.time() - t_ppo, 1)
    _wire_time(jid, "ppo", time.time() - t_ppo)
    train_b.add("steps", f"{chunk_rows(chunks)}/{total_steps}（训练/池子）")
    train_b.add("chunks", chunk_count(chunks))
    train_b.add("loader", loader_desc(chunks))
    train_b.add("kl", agg.get("kl"))
    # D8/D10：逐步 KL 与 clip fraction（旧 worker 无键 ⇒ 回落 kl / 0）。
    per_step_kl = agg.get("per_step_kl", agg.get("kl"))
    train_b.add("per_step_kl", per_step_kl)
    train_b.add("clip", agg.get("clip_fraction"))
    ppo_line = train_b.text()
    if ppo_line:
        note_ppo_tail(jid, ppo_line)
    return ppo_sec


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

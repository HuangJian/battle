"""
ppo.py — Clipped PPO for the Battle-City student (承接 P1.5 蒸馏 → RL).

Consumes trajectory shards written by `tools/sim/export-rl-rollout.ts`
(per-episode npy: obs/scalars/a_*/lp_*/value/reward/done/mask) and runs
clipped PPO with a shared trunk (warm-started from the DAgger BC checkpoint)
+ a value head.

Two modes:
  --init-from BC_WEIGHTS --out RL_WEIGHTS
      Build PPOStudent, warm-start policy heads from the BC checkpoint
      (value head stays random), save RL weights. Used once before the loop.
  --resume RL_WEIGHTS --data TRAJ_DIR --out RL_WEIGHTS --epochs K
      Load RL weights (policy+value), run K PPO epochs over the collected
      trajectories, overwrite RL weights. Called once per RL iteration.

Weight format is the canonical JSON+base64 (`weights_io.save_weights_json`),
so the TS runtime reloads it byte-for-byte.

Usage (via the unified launcher — bun dashboard/src/launch/cli.ts — which provides the venv + torch):
  python ppo.py --init-from tmp/student-weights-dagger/weights.json \
      --out tmp/rl-weights/weights.json
  python ppo.py --resume tmp/rl-weights/weights.json \
      --data tmp/rl-traj/it1 --out tmp/rl-weights/weights.json --epochs 4
"""

from __future__ import annotations

# 仓库根探测（B4，2026-09-02）：包已安装（pip install -e .）或 script-dir/cwd 在
# nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入 sys.path——
# 不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正 import，
# 避免探针导入产生 F401。
import importlib.util as _ilu

if _ilu.find_spec("schema") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import time
from typing import Any

import numpy as np
import numpy.typing as npt
import torch
import torch.nn as nn
import torch.nn.functional as F

from data.weights_io import load_weights_json, save_weights_json
from models.student import PPOStudent

# 共享 PPO 基础设施（ppo_common.py；行为与旧内联实现逐字节一致，见其模块 doc）。
from ppo.common import (
    _ppo_load,
    _ppo_save,
    approx_kl_est,
    cat_entropy,
    cat_logprob,
    chunk_episodes,
    compute_gae,
    demo_index,
    discover_shards,
    is_xla,
    load_episodes_common,
    load_shard_fields,
    log,
    masked_logsoftmax,
    optimizer_step,
    sync_scalars,
    xla_delta_str,
    xla_device_speed_probe,
    xla_fingerprint,
    xla_mark_step,
    xla_metrics_delta,
    xla_metrics_snapshot,
)
from ppo.trainer import aggregate_stats, tensored_chunks
from schema import FIRE_DIM, MOVE_DIM

# ---------------- hyper-params (CLI-overridable) ----------------
# R6（2026-08-25 训练质量审计）：it1–it68 未收敛（winRate ~10% 水平、value 预测量级
# ~0.03-0.09 vs 回报 0.1-0.3 → GAE 优势被噪声主导、policy loss≈0）。两处收紧：
#   GAMMA 0.99 → 0.995：决策间隔 K=10 下有效时域从 ~100 决策(16.7s) 拉长到 ~200
#     决策(33s)——守家/拦截是长时域行为，需要更远的信用回溯；
#   VF_COEF 0.5 → 1.0：价值头训练强度翻倍，缩小 value loss 与 policy loss 的量级差，
#     让 baseline 脱离噪声、给策略梯度注入真实优势信号。
GAMMA = 0.995
LAM = 0.95
CLIP_EPS = 0.2
VF_COEF = 1.0
# ★ ENT_COEF 只是**缺省值**（`ppo_update(ent_coef=None)` 时生效，缺省路径数学逐字节不变）。
#   2026-09-11：课程 `ppo_schedule[].ent_coef` 已可覆盖本常量。
#   动机（实测）：per-tick 五条腿里唯一学动的 c4-kb1 熵保持 0.71–0.74；其余四条
#   （c4-margin 0.709→0.490 / c4-dodge / c5-margin / c6-margin）全卡在 0.44–0.51，
#   合计 191 轮零趋势。同仓 intent 线在 2026-08-27 就因熵坍缩把熵正则 0.02→0.08
#   （见 intent.py:70 注释）、goal 线同步 0.08 —— 只有 per-tick 还停在 0.01（低 8×）。
ENT_COEF = 0.01
LR = 3e-4
MAX_GRAD_NORM = 1.0
MASK_DIM = MOVE_DIM + FIRE_DIM  # 7 (v2: item head removed)

# Observability cadences (pure logging; never touches RNG or numerics).
LOAD_LOG_EVERY = 128  # shard-loading progress lines
HB_SEC = 60.0  # PPO update heartbeat interval


def build_ppo(weights_path: str | None) -> PPOStudent:
    h = d = head_hidden = None
    if weights_path and os.path.exists(weights_path):
        meta, _ = load_weights_json(weights_path)
        a = meta.get("arch", {})
        h = a.get("h", 64)
        d = a.get("d", 8)
        head_hidden = a.get("head_hidden", 128)
    return PPOStudent(h=h or 64, d=d or 8, head_hidden=head_hidden or 128)


# ---------------- trajectory loading ----------------
# RL shard 字段表：{key: (filename, dtype)} —— 与旧 load_shard 逐字段一致（copy=False 零拷贝）。
# plan/rl-training-config.md §4.2：per-tick shard 的 reward 由 TS 落盘改为 Python
# 公式引擎计算——TS 只落 `metrics.npy`（[N+1,21] f8：N 个决策快照 + 1 个终局快照），
# 加载器读 holder（rl.reward_context）的 RewardFn 按配置公式算 reward。
_RL_SHARD_SPEC: dict[str, tuple[str, npt.DTypeLike]] = {
    "obs": ("obs.npy", np.uint8),
    "scalars": ("scalars.npy", np.float32),
    "a_move": ("a_move.npy", np.int64),
    "a_fire": ("a_fire.npy", np.int64),
    "lp_move": ("lp_move.npy", np.float32),
    "lp_fire": ("lp_fire.npy", np.float32),
    "value": ("value.npy", np.float32),
    "metrics": ("metrics.npy", np.float64),
    "done": ("done.npy", np.int64),
    "mask": ("mask.npy", np.int64),
}


def discover_rl_shards(root: str) -> list[str]:
    return discover_shards(root, ("metrics.npy", "obs.npy"))


def load_shard(dirpath: str) -> dict[str, np.ndarray]:
    """读 shard 字段 + manifest → `reward` 由公式引擎按 metrics 算（N 样本）。

    无 holder（reward_fn None）时**响亮报错**——旧 reward.npy 直读路径已随
    TS 落盘改版删除（历史 run 需在公式引擎下重采，plan §4.2 / §12-2）。
    """
    d = load_shard_fields(dirpath, _RL_SHARD_SPEC)
    metrics = d.pop("metrics")  # [N+1,21]
    n_obs = int(d["obs"].shape[0])
    if metrics.shape[0] != n_obs + 1:
        raise ValueError(
            f"metrics 行数 {metrics.shape[0]} != nSamples+1={n_obs + 1}（{dirpath}）——"
            "指标行失配（决策行 + 终局行），shard 损坏或格式不符"
        )
    manifest: dict = {}
    mp = os.path.join(dirpath, "manifest.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
    d["reward"] = _reward_from_metrics(metrics, manifest, dirpath)
    # stage：供 load_episodes_common 的**逐关**配额（target_transitions 路线）分组。
    # 它不进 episode 字段集（load_episodes_common 显式排除 "stage"），也不是 GAE 输入；
    # 0 维数组 ⇒ trim_shard_arrays 不会截它。manifest 缺该键时取 -1（所有 shard 归一组，
    # 配额退化为全局，加载日志里会显示只有 1 个 stage）。
    d["stage"] = np.asarray(int(manifest.get("stage", -1)))
    return d


def _reward_from_metrics(metrics: np.ndarray, manifest: dict, dirpath: str) -> np.ndarray:
    """metrics [N+1,29] + manifest {outcome, score} → reward [N]（float32）。

    wrapper（§4.3.3）：Φ = formula(metrics)；r[i] = Φ[i+1]−Φ[i]；末样本 +=
    reconcile（score_reconcile → scale·score(gated)−(Φ[N]−Φ[0])；toy → terminal）。
    """
    from rl.reward_context import current as _ctx_current
    from rl.reward_library import METRICS_DIM

    ctx = _ctx_current()
    m = np.asarray(metrics, dtype=np.float64)
    if m.ndim != 2 or m.shape[1] != METRICS_DIM:
        raise ValueError(
            f"metrics 形状应为 [N+1,{METRICS_DIM}]，收到 {m.shape}（{dirpath}）——metrics_version 不匹配？"
        )
    mver = manifest.get("metrics_version")
    if mver is not None and int(mver) != ctx.metrics_version:
        raise ValueError(
            f"shard metrics_version={mver} != 期望 {ctx.metrics_version}（{dirpath}）——"
            "shard 格式版本不匹配，禁止静默错读（评审 LC §1.1）"
        )
    if ctx.reward_fn is None:
        raise RuntimeError(
            f"metrics shard 需要 RewardFn，但 reward_context holder 未设置（{dirpath}）——"
            "per-tick 训练请经 `python run_rl.py --course <name>` 启动（奖励唯一定义源=课程配置公式）"
        )
    outcome = str(manifest.get("outcome", "timeout"))
    score = float(manifest.get("score", 0.0))
    r = ctx.reward_fn(m, outcome, score, ctx.it)
    return np.asarray(r, dtype=np.float32)


def load_episode_from_shard(dirpath: str, gamma: float = GAMMA, lam: float = LAM) -> dict | None:
    """流式 backend 接口（rl/stream.py）：单个 shard → 可训练 episode（adv/ret 未归一）。

    ppo_intent.load_episode_from_shard 同签名——run_rollout_stream 以 backend 参数
    复用同一套流式基础设施（工程化共享，勿在 stream 内复制第二份加载逻辑）。
    """
    d = load_shard(dirpath)
    N = d["obs"].shape[0]
    if N == 0:
        return None
    adv, ret = compute_gae(d["reward"], d["value"], d["done"], gamma, lam)
    return {
        "obs": d["obs"],
        "scalars": d["scalars"],
        "a_move": d["a_move"],
        "a_fire": d["a_fire"],
        "lp_move": d["lp_move"],
        "lp_fire": d["lp_fire"],
        "value": d["value"],
        "adv": adv.astype(np.float32),
        "ret": ret.astype(np.float32),
        "mask": d["mask"],
    }


def load_episodes(
    data_root: str,
    gamma: float = GAMMA,
    lam: float = LAM,
    normalize_adv: bool = True,
    normalize_ret: bool = False,
    per_stage_quota: int = 0,
) -> list[dict]:
    """Discover trajectory shards under `data_root`, compute per-episode GAE,
    and normalize advantages across the whole batch. Shared by this CLI's
    update mode and the run_rl.py loop.

    normalize_ret（R5，默认 False = 历史行为逐字节不变）：True 时 ret 跨 batch
    归一（mean 0 / std 1，intent 头同款），让 value 头拟合 O(1) 量级目标。
    备注：GAE 自举仍用 rollout 时 head 输出的原始尺度 V——baseline 不改变策略
    梯度的无偏性，只改变方差；当前 V 近乎常数（MSE≫return 方差）时归一化只会
    把 baseline 从"无"变"有"，不会变坏。stream 路径不走本函数（见
    rl/stream.py），该 flag 只覆盖串行（local/remote）路径。
    """
    return load_episodes_common(
        data_root,
        label="ppo",
        shard_kind="RL",
        need_files=("metrics.npy", "obs.npy"),
        shard_loader=load_shard,
        gae=lambda d: compute_gae(d["reward"], d["value"], d["done"], gamma, lam),
        gae_name="GAE",
        normalize_adv=normalize_adv,
        normalize_ret=normalize_ret,
        per_stage_quota=per_stage_quota,
    )


# ---------------- PPO update ----------------
# checkpoint 原语（_pack_np_state/_unpack_np_state/_ppo_save/_ppo_load）与
# chunk_episodes 由 ppo_common 提供（re-export 见顶部 import），行为逐字节一致。


def _demo_masked_ce(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """合法类掩码 CE，与 train.bc._masked_ce 同数学（非法类 -1e9 进分母剔除；
    合法类 <2 的样本跳过；全跳过时返回零梯度标量）。PPO 内联一份，避免 engine
    反向依赖 train.bc 的 CLI 模块（数值口径以本函数 + 单测为准）。"""
    m = mask > 0
    keep = m.sum(dim=-1) >= 2
    if not keep.any():
        return logits.sum() * 0.0
    z = logits.masked_fill(~m, -1e9)
    per = F.cross_entropy(z[keep], target[keep], reduction="none")
    return per.mean()


def ppo_update(
    model,
    opt,
    chunks,
    epochs,
    device,
    ckpt_path: str | None = None,
    on_epoch_done=None,
    kl_coef: float = 0.0,
    ref_model=None,
    kickstart_kl: float = 0.0,
    ent_coef: float | None = None,
    demo_bank: dict | None = None,
    demo_bc_coef: float = 0.0,
    demo_per_mb: int = 0,
):
    """chunks: list of minibatch dicts (obs (B,14,26,26) / scalars (B,24) / ...).

    ckpt_path: 非空则每 epoch 落盘 checkpoint（model/opt/epochs_done/numpy RNG），
    并支持断点续跑（重启后从已完成 epoch 数继续，minibatch 乱序精确复现）。
    on_epoch_done(ep_done, model): 每个 epoch 完成后同步回调（双缓冲提前预采的
    触发点——stream 在 epoch3 完成时把 θ_{N,e3} 存盘 spawn 首波预采，PPO 继续
    最后一个 epoch；模型在此处即当前 epoch 训练完的状态）。

    kl_coef（plan/rl-training-config.md §8，M1c）：update 期新增形参，默认 0.0
    （向后兼容，缺省路径数学逐字节不变）。>0 时对每个 minibatch 施加对采样策略
    （lp_old，即收集策略）的 KL 惩罚 `kl_coef · E[(r−1) − ln r]`——per-tick 的
    「初始 KL 大、稳定后衰减」由 ppo_schedule 显式传值落地。

    ent_coef（2026-09-11 接线，与 kl_coef 同一路子）：熵正则系数。None = 用模块常量
    ENT_COEF（0.01，缺省路径数学逐字节不变）；显式给值则 `loss -= ent_coef · entropy`。
    动机：per-tick 线熵坍缩（唯一学动的 c4-kb1 熵 0.71–0.74，其余四条卡 0.44–0.51）。

    ref_model + kickstart_kl（R5§363，BC-anchored kickstart，mirror intent 数学）：
    ref = BC 冻结快照（调用方 freeze＋eval）；两者就绪时 loss +=
    kickstart_kl · KL(π_curr ‖ π_BC)，分头 exact-KL（move＋fire 求和，与 entropy
    口径同构）。任一缺席即零开销恒等（默认路径数学逐字节不变）。

    demo_bank + demo_bc_coef + demo_per_mb（demo 混 batch，x20 后续）：
    demo_bank = {obs (N,16,26,26) u1 / scalars (N,30) f4 / actions (N,2) i64
    [move,fire] / masks (N,7)}（与 rollout 同 ObsEncoder v3 口径）；每 PPO minibatch
    步从 bank 均匀抽 demo_per_mb 个样本（np RNG ⇒ ckpt 断点续跑精确复现），
    loss += demo_bc_coef · (CE_move + CE_fire)（合法类掩码 CE，与 train.bc._masked_ce
    同数学：非法类 -1e9、单合法类样本跳过）。三者任一缺席/非正即关闭，默认路径
    数学逐字节不变（含额外 stat 键 demo_bc，关闭时恒 0.0）。
    """
    model.train()
    clip = CLIP_EPS
    # 熵正则系数：None → 模块缺省（数学与接线前逐字节一致）；显式给值 → 该项被覆盖。
    ent_c = ENT_COEF if ent_coef is None else float(ent_coef)
    stats: list[dict[str, float]] = []
    # Convert numpy -> torch ONCE per chunk (not once per epoch): identical
    # values, ~epochs× less conversion overhead.
    tensored = tensored_chunks(chunks, device)
    total_steps = len(tensored) * epochs
    # ---- XLA 步耗诊断（2026-09-22，纯观测；非 XLA 机器零开销）----
    # 见 ppo/common.xla_metrics_snapshot 的说明：把「是不是每个 chunk 迭代都触发一次
    # 新编译」从猜测变成日志里的读数。PPO_XLA_DIAG=0 可关；基线与自检都取在循环之前，
    # 免得把设备自检那一发编译算到第一步头上。
    diag_prev: dict[str, float] = {}
    diag_by_b: dict[int, dict[str, float]] = {}
    diag_first = 12  # 前 N 步逐步取；之后每 EVERY 步取一次
    diag_every = 16
    diag_last_at = 0
    diag_wall = 0.0
    diag_compile = 0.0
    diag_uncached = 0.0
    # 环境开关关掉时一次快照都不取（非 XLA 机器上快照本身也返空）。
    _diag_base = (
        xla_metrics_snapshot()
        if os.environ.get("PPO_XLA_DIAG", "1") not in ("0", "false", "False")
        else {}
    )
    if _diag_base:
        diag_first = max(1, int(os.environ.get("PPO_XLA_DIAG_FIRST", "12")))
        diag_every = max(1, int(os.environ.get("PPO_XLA_DIAG_EVERY", "16")))
        _fp = xla_fingerprint(device)
        log(
            f"[ppo] XLA 步耗诊断开启（PPO_XLA_DIAG=0 关闭）：device={device} "
            f"device_type={_fp.get('device_type')} 设备数={_fp.get('global_device_count')} "
            f"attrs={_fp.get('attrs')} 自检matmul2048={xla_device_speed_probe(device)}s"
        )
        log(
            f"[ppo] 判读口径：新编译=1 且编译秒级 ⇒ 图签名每步都在变；新编译=0/命中>0"
            f" 而墙钟仍秒级 ⇒ 病不在编译。采样节奏：前 {diag_first} 步逐步、之后每 "
            f"{diag_every} 步一次（期间只累计墙钟，delta 是累积的）"
        )
        # 基线与设备自检都取在循环之前——别把自检那一发编译算到第一步头上。
        diag_prev = xla_metrics_snapshot()
    diag_on = bool(diag_prev)
    # ---- demo bank 物化（一次 numpy→torch；关闭时零开销） ----
    demo_on = (
        demo_bank is not None and float(demo_bc_coef) > 0.0 and int(demo_per_mb) > 0
    )
    demo_t: dict[str, Any] = {}
    demo_n = 0
    # 复用的设备索引缓冲（见 ppo/common.demo_index）——XLA 上「每步现造 host 索引」会让
    # 每步都是一张新图 ⇒ 每步一次全图重编译（真机实录 8~10s/步，编译命中时 0.31s/步）。
    demo_idx_dev: torch.Tensor | None = None
    if demo_on:
        assert demo_bank is not None  # 由 demo_on 保证，mypy 收窄用
        try:
            demo_t = tensored_chunks(
                [
                    {
                        "obs": np.asarray(demo_bank["obs"]),
                        "scalars": np.asarray(demo_bank["scalars"]).astype(np.float32),
                        "actions": np.asarray(demo_bank["actions"]).astype(np.int64),
                        "masks": np.asarray(demo_bank["masks"]).astype(np.float32),
                    }
                ],
                device,
            )[0]
            demo_n = int(demo_t["actions"].shape[0])
            demo_on = demo_n > 0
            if demo_on:
                demo_idx_dev = torch.zeros(
                    int(demo_per_mb), dtype=torch.int64, device=device
                )
                # 自描述标记：日志里没这行就说明跑的包里没有这个修复（别拿旧包的结果判修法）。
                log(
                    f"[ppo] demo 索引复用缓冲已启用（per_mb={int(demo_per_mb)}, int64, "
                    f"{device}）——每步 copy_ 就地改值，避免 XLA 每步重编译"
                )
        except (KeyError, ValueError, TypeError) as err:
            raise ValueError(f"[ppo] demo_bank 字段缺失/形状非法——拒收（{err}）") from err
    log(
        f"[ppo] update start: {len(tensored)} chunks x {epochs} epochs (~{total_steps} grad steps)"
        + (f" + demo_bc(coef={float(demo_bc_coef):g}, per_mb={int(demo_per_mb)}, N={demo_n})" if demo_on else "")
    )
    t0 = time.time()
    last_hb = t0
    # ---- kickstart ref 前向预计算（2026-09-10，纯吞吐、数值逐位不变） ----
    # ref_model 是冻结的（eval + requires_grad=False）且 StudentNet BN-free ⇒ 无状态；
    # ref 输出只依赖 (obs, scalars, mask)，而这三者**逐 chunk 固定、跨 epoch 不变**
    # （epoch 只重排 chunk 顺序，不改内容）。原实现在最内层每步重算一次完整前向 =
    # epochs× 白烧：本机 CPU 实测 ref 前向占单步 ~44%（tmp/bench-ppo-throughput.py 的
    # D-B 差：6.65 -> 9.59 s/step）。此处每个 chunk 只算一次并按索引复用，
    # ref 成本降到 1/epochs，数值与原实现逐位相同。
    # 内存代价：len(chunks) x B x 7 floats ≈ 37x512x7x4B ≈ 0.5MB（可忽略）。
    ref_cache: list[tuple[torch.Tensor, torch.Tensor] | None] = [None] * len(tensored)
    if ref_model is not None and kickstart_kl > 0:
        # 2026-09-11 慢 job（TPU 45~52 s/step，eta~5.9h）修复：落执行边界，否则
        # 这段惰性图一直挂着，会与首个训练步的图拼成巨型图（首步 ~24s 编译 +
        # 图签名漂移）。对齐探针 build_ref_cache 的既有行为（tools/tpu-probe.py E 段）。
        # 2026-09-16 修正：边界从「整段一次」改为「**每 chunk 一次**」。整段一次时
        # XLA 把 Python 循环里 N 次前向拼成**一张**图，图签名含 len(chunks)——每轮
        # transition 数不同 ⇒ chunk 数（实录 47/48/46/45）每轮都变 ⇒ **每个 job
        # 冷编译一次 ~50-80s**（同轮 epoch1 83s vs epoch2-4 合计 7s，即编译后飞快）。
        # 每 chunk 一次后图退化为「单 chunk 前向」，shape 恒定 (B,14,26,26)，首次
        # 编译后命中缓存，与 chunk 数无关。非 XLA 设备为 no-op，CPU/CUDA 数值
        # 逐位不变（执行时机对 eager 无感）；XLA 上 ref_model 冻结 + no_grad ⇒
        # 逐位不变（只改变图切分，不改变算子与数据）。
        # 例外已消除（2026-09-23，见 chunk_episodes 的 mb 对齐）：旧行为下末 chunk
        # 是 ragged（B' = n % mb，随每轮 transition 数漂移）⇒ 每轮/每 epoch 各付一次
        # 小 shape 编译（§134 的 ~14s、§140 实测 12.5s × 4）。现在池子先裁到 mb 的
        # 整数倍，图形状恒为 (mb,14,26,26)，"每 chunk 一次 mark" 就足以覆盖全部步。
        _t_ref = time.time()
        with torch.no_grad():
            for _i, _e in enumerate(tensored):
                _rm, _rf, _ = ref_model(_e["obs"], _e["scalars"])
                _m = _e["mask"]
                ref_cache[_i] = (
                    masked_logsoftmax(_rm, _m[:, :MOVE_DIM]),
                    masked_logsoftmax(_rf, _m[:, MOVE_DIM : MOVE_DIM + FIRE_DIM]),
                )
                xla_mark_step(device)
        # 注意：日志必须在 mark_step **之后**打——惰性 XLA 下循环里一次前向都没真跑，
        # 打在前面得到的是 0s 的假数字（这正是本次误判的由来）。
        log(
            f"[ppo] kickstart ref 预计算完成：{len(tensored)} chunks（原每 epoch 重算）"
            f"，{time.time() - _t_ref:.1f}s（{device} 执行实耗，XLA 含编译）"
        )
    # ---- XLA 预物化（2026-09-22）：chunk/demo/索引张量必须在**进入循环前**落设备 ----
    # host→device 的惰性转移若发生在「首次使用」那一刻，就会随该步的图一起被内联成新签名
    # ⇒ 每个 chunk 的首次使用各付一次全图编译（真机实录：epoch1 累计 41 次新编译、
    # 单步 5.5s；epoch2 之后降到 0.9~2.4s/步——正是「变体集被填满」的形态）。
    # 一次 mark 把全部待转移张量落盘，后续每步都只是同一张图 ⇒ 编译只付一次。
    # 非 XLA 设备为 no-op（xla_mark_step 内部判断），CPU/CUDA 数值与行为逐位不变。
    if is_xla(device):
        xla_mark_step(device)
        if diag_on:
            log(
                f"[ppo] XLA 预物化：{len(tensored)} 个 chunk + demo 张量已落设备"
                f"（循环前一次 mark；否则每个 chunk 首次使用各付一次全图编译）"
            )
    start_epoch = _ppo_load(ckpt_path, model, opt)
    if start_epoch:
        log(
            f"[ppo] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done "
            f"(continuing remaining {epochs - start_epoch})"
        )
    for ep in range(start_epoch, epochs):
        perm = np.random.permutation(len(tensored))
        n_ep_start = len(stats)
        for j, i in enumerate(perm):
            _step_t0 = time.time()
            e = tensored[int(i)]
            obs = e["obs"]
            sc = e["scalars"]
            a_move = e["a_move"]
            a_fire = e["a_fire"]
            lp_move = e["lp_move"]
            lp_fire = e["lp_fire"]
            adv = e["adv"]
            ret = e["ret"]
            mask = e["mask"]  # (T, 7)

            mv, fr, val = model(obs, sc)
            move_logp = masked_logsoftmax(mv, mask[:, :MOVE_DIM])
            fire_logp = masked_logsoftmax(fr, mask[:, MOVE_DIM : MOVE_DIM + FIRE_DIM])

            lp_new = cat_logprob(a_move, move_logp) + cat_logprob(a_fire, fire_logp)
            lp_old = lp_move + lp_fire

            ratio = torch.exp(lp_new - lp_old)
            surr1 = ratio * adv
            surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
            policy_loss = -torch.min(surr1, surr2).mean()

            value_loss = F.mse_loss(val.squeeze(-1), ret)
            entropy = cat_entropy(move_logp) + cat_entropy(fire_logp)

            loss = policy_loss + VF_COEF * value_loss - ent_c * entropy
            if kl_coef > 0.0:
                # 对采样策略的 KL 惩罚（与 approx_kl_est 同估计量，可微项）
                loss = loss + kl_coef * ((ratio - 1.0) - (lp_new - lp_old)).mean()
            kick_mean = torch.zeros((), device=device)
            _ref = ref_cache[int(i)]
            if _ref is not None:
                # 预计算缓存（见上方 ref_cache）：数值与「此处现算 ref_model」逐位相同，
                # 但每 chunk 只付一次前向而非每 epoch 一次。
                ref_move, ref_fire = _ref
                kl_m = (move_logp.exp() * (move_logp - ref_move)).sum(dim=-1)
                kl_f = (fire_logp.exp() * (fire_logp - ref_fire)).sum(dim=-1)
                kick_mean = (kl_m + kl_f).mean()
                loss = loss + kickstart_kl * kick_mean
            demo_bc_mean = torch.zeros((), device=device)
            if demo_on:
                # np RNG ⇒ 与 chunk permutation 同一条 ckpt 流，断点续跑精确复现。
                _didx = np.random.randint(0, demo_n, size=int(demo_per_mb))
                # 索引必须先落到**复用的设备张量**上再索引（否则每步一张新图 ⇒ 每步重编译，
                # 见 ppo/common.demo_index）；抽样本的数与序完全不变。
                _didx_t = demo_index(demo_idx_dev, _didx)
                _dm, _df, _ = model(demo_t["obs"][_didx_t], demo_t["scalars"][_didx_t])
                _dmm = demo_t["masks"][_didx_t]
                _dact = demo_t["actions"][_didx_t]
                demo_bc_mean = _demo_masked_ce(
                    _dm, _dact[:, 0], _dmm[:, :MOVE_DIM]
                ) + _demo_masked_ce(_df, _dact[:, 1], _dmm[:, MOVE_DIM : MOVE_DIM + FIRE_DIM])
                loss = loss + float(demo_bc_coef) * demo_bc_mean

            opt.zero_grad()
            loss.backward()
            gn = nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
            optimizer_step(opt, device)

            with torch.no_grad():
                # 一次同步取全部 8 个标量。原实现逐项取 8 次 = CUDA 上 8 次全设备
                # 同步（每次 drain 队列，把 CPU/GPU 流水线串起来）——详见
                # ppo/common.sync_scalars 的说明。数值逐位不变。
                stats.append(
                    sync_scalars(
                        {
                            "policy": policy_loss,
                            "value": value_loss,
                            "entropy": entropy,
                            "kl": approx_kl_est(lp_old, lp_new),
                            "kickstart": kick_mean,
                            "demo_bc": demo_bc_mean,
                            "mean_ret": ret.mean(),
                            "mean_adv": adv.mean(),
                            "gnorm": gn,
                        }
                    )
                )
            # 2026-09-11 根因修复（TPU）：每步强制图执行边界。torch_xla 惰性模式下
            # materialize（.tolist()）只断言其依赖子图，backward/optimizer 环节的
            # 在途节点不 drain，跨步骤累积 → 图线性膨胀 → 单步耗时随步数线性增长
            # （实录：TPU 24→45→52s/step 递增；探针 E 段 1.7→13→23s；E2 10s）。
            # 每步显式 mark 后 TPU 单步稳定 ~44ms（探针 E1+mark 实测收敛）。非 XLA
            # 设备为 no-op，CPU/CUDA 数值与行为逐位不变。
            xla_mark_step(device)
            now = time.time()
            # XLA 步耗读数（必须在 mark_step 之后：编译/执行都已 drain，否则恒为 0）。
            # 未取样的步只累计墙钟——delta 是累积的，中间发生的编译仍会落在下一次读数里。
            if diag_on:
                _done = ep * len(tensored) + j + 1
                _wall_s = now - _step_t0
                diag_wall += _wall_s
                _acc = diag_by_b.setdefault(int(obs.shape[0]), {"iters": 0.0, "wall": 0.0})
                _acc["iters"] += 1
                _acc["wall"] += _wall_s
                if _done <= diag_first or _done % diag_every == 0 or _done == total_steps:
                    _snap = xla_metrics_snapshot()
                    _dlt = xla_metrics_delta(diag_prev, _snap)
                    diag_prev = _snap
                    _cmp = float(_dlt.get("t.CompileTime", 0.0))
                    _unc = int(_dlt.get("c.UncachedCompile", 0.0))
                    diag_compile += _cmp
                    diag_uncached += float(_unc)
                    _span = _done - diag_last_at
                    diag_last_at = _done
                    _span_wall = diag_wall
                    diag_wall = 0.0
                    log(
                        f"[ppo] diag s={_done}/{total_steps} 窗口={_span}步/{_span_wall:.2f}s"
                        f"(单步均{_span_wall / max(_span, 1):.3f}s) 图签名=B{int(obs.shape[0])}"
                        f"/demo{int(demo_per_mb) if demo_on else 0}"
                        f"/kl{int(kl_coef > 0.0)}/ref{int(_ref is not None)}"
                        f"/demo{int(demo_on)} {xla_delta_str(_dlt)}"
                    )
                    # 判定只在「编译占比落在 (50%, 100%] 这个物理上说得通的范围」时打。
                    # XLA 的 metrics 会被重置（新 shape 出现/缓存事件）⇒ delta 可能为负或
                    # 超过窗口墙钟（实录 编译=73.93s / 追踪=-52.57s）；那种窗口只信
                    # 墙钟与「新编译次数」，不给百分比结论，免得误导。
                    if 0.5 * _span_wall < _cmp <= _span_wall:
                        log(
                            f"[ppo] diag 判定：编译占本窗口 {100.0 * _cmp / _span_wall:.0f}%"
                            f"（新编译 {_unc} 次）⇒ 墙钟买的是**编译**，不是算力"
                        )
                    elif _cmp > _span_wall:
                        log(
                            f"[ppo] diag 注意：本窗口 {_unc} 次新编译，但编译耗时读数"
                            f"({_cmp:.1f}s) > 窗口墙钟({_span_wall:.1f}s) ⇒ XLA metrics "
                            f"被重置，耗时数值不可信（只看「新编译次数」与墙钟）"
                        )
            # Heartbeat: pure-print progress/health line; wall-clock only.
            if now - last_hb >= HB_SEC:
                last_hb = now
                recent = stats[-32:]
                n_r = len(recent)
                done_steps = ep * len(tensored) + j + 1
                elapsed = now - t0
                eta = elapsed / done_steps * (total_steps - done_steps)
                log(
                    f"[ppo] ep {ep + 1}/{epochs} chunk {j + 1}/{len(tensored)} "
                    f"step {done_steps}/{total_steps} "
                    f"elapsed={elapsed:.0f}s eta~{eta:.0f}s "
                    f"kl={sum(s['kl'] for s in recent) / n_r:.4f} "
                    f"entropy={sum(s['entropy'] for s in recent) / n_r:.4f} "
                    f"policy={sum(s['policy'] for s in recent) / n_r:.4f} "
                    f"value={sum(s['value'] for s in recent) / n_r:.4f} "
                    f"gnorm={sum(s['gnorm'] for s in recent) / n_r:.3f}"
                )
        if ckpt_path:
            _ppo_save(ckpt_path, model, opt, ep + 1)
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
        ep_stats = stats[n_ep_start:]
        n_e = max(1, len(ep_stats))
        log(
            f"[ppo] epoch {ep + 1}/{epochs} done ({time.time() - t0:.0f}s total, "
            f"{len(ep_stats)} chunks)"
            + (", ckpt saved" if ckpt_path else "")
            + f": kl={sum(s['kl'] for s in ep_stats) / n_e:.4f} "
            f"entropy={sum(s['entropy'] for s in ep_stats) / n_e:.4f} "
            f"kickstart={sum(s['kickstart'] for s in ep_stats) / n_e:.4f} "
            f"policy={sum(s['policy'] for s in ep_stats) / n_e:.4f} "
            f"value={sum(s['value'] for s in ep_stats) / n_e:.4f} "
            f"gnorm={sum(s['gnorm'] for s in ep_stats) / n_e:.3f}"
        )
        if diag_on:
            # 按 chunk batch 形状汇总（累计）：若 B 只有一个值却仍「每步新编译」⇒ 形状不是原因。
            _sum = " ".join(
                f"B={_b}:{int(_a['iters'])}步/墙钟{_a['wall']:.0f}s"
                for _b, _a in sorted(diag_by_b.items(), key=lambda kv: -kv[1]["wall"])
            )
            log(
                f"[ppo] diag 累计汇总（到 epoch {ep + 1}）：{_sum} | "
                f"累计编译={diag_compile:.0f}s（新编译 {diag_uncached:.0f} 次）/ "
                f"总墙钟={time.time() - t0:.0f}s"
            )
    # aggregate
    if not stats:
        # 断点续跑"剩余 0 epoch"路径（checkpoint 已完成）：无梯度步可跑，
        # 返回零聚合——此前 stats[0] 直接 IndexError 让整轮重试空转。
        log("[ppo] checkpoint already complete — 0 grad steps, returning zero aggregate")
        return {
            "policy": 0.0,
            "value": 0.0,
            "entropy": 0.0,
            "kl": 0.0,
            "kickstart": 0.0,
            "demo_bc": 0.0,
            "gnorm": 0.0,
            "mean_ret": 0.0,
        }
    agg = aggregate_stats(stats, list(stats[0].keys()))
    return agg


# rl/stream.py 的 backend 契约要求模块暴露 update(...)（intent 的 ppo_intent 已有）；
# 普通训练器的流式路径（run_rl.py --stream 1）此前从未被拉通——补此别名。
# 签名与 ppo_update 完全一致（ckpt_path 经 update_kwargs 透传）。
update = ppo_update


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--init-from", type=str, default=None, help="BC weights to warm-start from (init mode)"
    )
    ap.add_argument("--resume", type=str, default=None, help="RL weights to resume (update mode)")
    ap.add_argument("--data", type=str, default=None, help="trajectory shard root (update mode)")
    ap.add_argument("--out", type=str, required=True, help="output weights path")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument(
        "--mb", type=int, default=512, help="minibatch size (transitions per update step)"
    )
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--gamma", type=float, default=GAMMA)
    ap.add_argument("--lam", type=float, default=LAM)
    ap.add_argument("--device", type=str, default="cpu")
    ap.add_argument(
        "--normalize-ret",
        type=int,
        default=0,
        help="R5：ret 跨 batch 归一（mean 0/std 1，value 头拟合 O(1) 目标）；"
        "0 = 历史行为（默认）。run_rl 课程模式由课程 normalize_ret 驱动，本 flag "
        "仅手动 update 用。",
    )
    ap.add_argument("--seed", type=int, default=7, help="numpy seed for minibatch shuffling")
    ap.add_argument(
        "--threads",
        type=int,
        default=8,
        help="torch intra-op threads; 0 keeps the launcher default "
        "(OMP_NUM_THREADS). 8 = physical cores on the dev box — "
        "avoids HT contention + OMP sync overhead on this small model.",
    )
    ap.add_argument(
        "--per-stage-quota",
        type=int,
        default=0,
        help=">0：逐关只收前 N 个 transition（target_transitions 路线的严格样本量配额；"
        "截断在 GAE 之前，逐关独立）。0 = 历史行为：全收（默认）。"
        "由 run_rl 按 ceil(target_transitions / 关数) 算好透传。",
    )
    args = ap.parse_args()

    np.random.seed(args.seed)
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    try:
        # Denormal floats can slow small-model CPU convs by large factors;
        # flushing them is numerically negligible (~0 values) and often much
        # faster. Not supported on every platform -> best effort.
        torch.set_flush_denormal(True)
    except (RuntimeError, AttributeError):
        pass
    device = torch.device(args.device)

    # ---- init mode ----
    if args.init_from and not args.data:
        model = build_ppo(args.init_from)
        # warm-start policy heads from BC (value head stays random)
        from data.weights_io import load_state_into

        load_state_into(model, args.init_from)
        save_weights_json(model, args.out)
        log(f"[ppo] init RL weights (BC policy + random value) -> {args.out}")
        log(f"[ppo] params={sum(int(p.numel()) for p in model.parameters())}")
        return

    # ---- update mode ----
    assert args.resume and args.data, "--resume and --data required in update mode"
    model = build_ppo(args.resume)
    from data.weights_io import load_state_into

    load_state_into(model, args.resume)
    model.to(device)

    episodes = load_episodes(
        args.data,
        args.gamma,
        args.lam,
        normalize_ret=bool(args.normalize_ret),
        per_stage_quota=int(args.per_stage_quota),
    )
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    log(f"[ppo] total transition steps={total_steps}")

    chunks = chunk_episodes(episodes, args.mb)
    log(f"[ppo] {len(episodes)} episodes -> {len(chunks)} minibatch chunks (mb={args.mb})")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    agg = ppo_update(model, opt, chunks, args.epochs, device)

    model.to("cpu")
    save_weights_json(model, args.out)
    log(
        f"[ppo] update done epochs={args.epochs} "
        f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
        f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} "
        f"mean_ret={agg['mean_ret']:.3f} -> {args.out}"
    )


if __name__ == "__main__":
    main()

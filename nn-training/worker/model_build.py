"""build_model —— RL 权重初始化（BC warm-start / RL resume / A4 归一校准）。

从 trainer/run_rl.py 拆出（瘦身，2026-09-02）：build_model 是训练路径首个需要 torch
的点（collect-only 子进程流程全程零 torch——**本模块必须保持顶层不 import torch**，
延迟导入在函数内）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parent.parent

from common.log import log
from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from common.schema import BOARD, OBS_CHANNELS, SCALAR_DIM
from worker.modes import get_backend

if TYPE_CHECKING:  # build_model 返回注解（future annotations 下不运行时求值）
    import torch


def _build_intent_or_goal(
    bc_path: str, rl_path: str, mode: str, workers: int
) -> torch.nn.Module:
    """intent/goal 的 B′ 三头迁移：无权重时从 BC CLI 初始化，有权重时续跑。"""
    import worker.ppo.goal as ppo_goal
    import worker.ppo.intent as ppo_intent

    PPO = get_backend(mode)
    resume = os.path.exists(rl_path)
    if not resume:
        # 2026-11-28 修复：flat 脚本已并包（commit 3c831690），旧路径
        # `nn-training/{ppo_intent,ppo_goal}.py` 已不存在，且 cwd=nn-training ⇒
        # 拼成 nn-training/nn-training/... 必然 FileNotFoundError（mode=intent/goal
        # 首启静默不可用）。改用模块入口 `-m worker.ppo.intent`：cwd 在 nn-training，
        # 既解析到包，又避免 `worker/ppo/common.py` 遮蔽顶层 `common` 包。
        module = "worker.ppo.goal" if mode == "goal" else "worker.ppo.intent"
        log(f"init RL weights from BC ({bc_path}) -> {rl_path} ({mode} backend)")
        subprocess.run(
            [
                sys.executable,
                "-m",
                module,
                "--init-from",
                bc_path,
                "--out",
                rl_path,
                "--threads",
                str(max(1, min(8, workers))),
            ],
            cwd=str(REPO_ROOT),
            check=True,
            **_POPEN_NO_WINDOW,
        )
    model = PPO.build_rl_net(rl_path)
    if mode == "goal":
        ppo_goal.load_goal_weights(model, rl_path)
    else:
        ppo_intent.load_intent_weights(model, rl_path)
    print(
        f"[{time.strftime('%H:%M:%S')}] [run_rl] "
        + ("resume" if resume else "init")
        + f" weights <- {rl_path if resume else bc_path} "
        f"({mode}, params={sum(int(p.numel()) for p in model.parameters())})"
        + ("" if resume else f" -> {rl_path}")
    )
    return model  # type: ignore[no-any-return]


def _bc_value_pretrained(bc_path: str) -> bool:
    """BC weights.json 的 `value_coef > 0` ⇒ value 头是 MC 预训练的，pipeline 别清零。"""
    try:
        from worker.data.weights_io import load_weights_json

        _meta, _ = load_weights_json(bc_path)
        return float((_meta.get("args") or {}).get("value_coef", 0) or 0) > 0
    except Exception as e:
        log(f"[run_rl] WARN cannot read bc meta for value flag: {e}")
        return False


def _sample_real_obs(n: int = 16) -> torch.Tensor:
    """真实 obs 校准样本：多个最近 shard 各取一层 + 合成极端（全零/全亮/条纹）。

    单一 shard 可能是退化样本（全暗 obs 曾让 feat_max=1，α 放大 14x 把已归一的
    trunk 再抬爆，2026-08-30 s1-cap 首启实测）。
    """
    import glob

    import numpy as np
    import torch

    paths = sorted(
        glob.glob(str(REPO_ROOT / "tmp" / "*" / "it*" / "**" / "obs.npy"), recursive=True),
        key=os.path.getmtime,
        reverse=True,
    )[:8]
    chunks: list[torch.Tensor] = []
    for p_ in paths:
        try:
            arr = np.load(p_, mmap_mode="r")
            # v3：通道数从 schema 常量判——写死 14 会让全部 v3 shard 被静默过滤。
            if arr.ndim == 4 and arr.shape[1] == OBS_CHANNELS and arr.shape[0] >= 1:
                chunks.append(torch.from_numpy(np.ascontiguousarray(arr[:n])))
        except Exception:
            continue
    synth = torch.zeros(3, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    synth[1] = 255
    synth[2, :, ::2] = 255
    chunks.append(synth)
    return torch.cat(chunks, dim=0)


def _scale_trunk(model, alpha: float, no_grad) -> None:
    """trunk 参数统一放大 alpha（ConvMixer 激活校准）。"""
    TRUNK = ("stem.", "blocks.", "fc.")
    with no_grad():
        for n, p_ in model.named_parameters():
            if n.startswith(TRUNK):
                p_.mul_(alpha)


def _scale_policy_and_value(model, beta: float, keep_value: bool, no_grad) -> None:
    """三个策略头统一缩放 beta；value 头未预训练则清零。"""
    with no_grad():
        for n, p_ in model.named_parameters():
            # B1 v6：hold 头必须与 move/fire 同缩放，漏了会留 kaiming 量级。
            if n.startswith(("move_head.", "fire_head.", "hold_head.")):
                p_.mul_(beta)
            elif n.startswith("value_head.") and not keep_value:
                p_.zero_()


def _warm_start_normalize(model, keep_value: bool = False) -> None:
    """BC 权重归一：trunk→h≈15，策略头 logit→~3（保 argmax、软先验），value 清零。"""
    import torch

    sample = _sample_real_obs(32)
    sc = torch.zeros(sample.shape[0], SCALAR_DIM)

    def _feat_max() -> float:
        with torch.no_grad():
            return float(model.features(sample, sc).abs().max()) + 1e-6

    def _logit_max() -> float:
        with torch.no_grad():
            out = model(sample, sc)
        # (move, fire[, hold], value): every policy head must share the
        # same logit-range normalization (B1 v6 adds hold).
        policy = out[:-1] if len(out) >= 3 else out[:2]
        return max(float(h.abs().max()) for h in policy) + 1e-6

    alpha = 15.0 / _feat_max()
    _scale_trunk(model, alpha, torch.no_grad)
    beta = 3.0 / _logit_max()  # 保 argmax 的软先验：logit 范围 ~3（熵≈1.2）
    _scale_policy_and_value(model, beta, keep_value, torch.no_grad)
    print(
        f"[run_rl] BC warm-start normalize: trunk x{alpha:.4g}, "
        f"policy heads x{beta:.4g} (logit range -> 3.0 soft prior), "
        f"value {'kept (MC-pretrained)' if keep_value else 'zeroed'}; "
        f"feat_max={15.0 / alpha:.0f}, logit_max_pre={3.0 / beta:.1f}"
    )


def build_model(
    bc_path: str, rl_path: str, mode: str = "per-tick", workers: int = 8
) -> torch.nn.Module:
    """Init once: warm-start policy heads from BC when no RL weights exist yet;
    otherwise resume from the existing RL weights (policy + trained value).
    The init path SAVES the merged weights to rl_path before returning — the
    TS rollout reads that file, so it must exist before iteration 1.

    mode 分模式（RL 入口整合，DECISIONS §307）：
      - per-tick：DAgger BC warm-start + A4 trunk 校准归一（warm_start_normalize）。
      - intent/goal：ppo_intent/ppo_goal CLI init-from（B′ 三头迁移，value 头
        随机），幂等；已存在则 load_intent_weights / load_goal_weights 续跑。

    torch 延迟导入（B7，2026-09-02）：本函数是训练路径首个需要 torch 的点——
    它之前的 collect-only 子进程流程（argparse / build_pairs / queue 派发）全程
    零 torch。
    """
    # 延迟导入：torch 及其依赖只在此刻加载（collect-only 路径永远到不了这里）
    import worker.ppo.engine as ppo_mod
    from worker.data.weights_io import load_state_into, save_weights_json

    if mode in ("intent", "goal"):
        return _build_intent_or_goal(bc_path, rl_path, mode, workers)

    resume = os.path.exists(rl_path)
    src = rl_path if resume else bc_path
    model = ppo_mod.build_ppo(src)
    if src:
        load_state_into(model, src)
    value_pretrained = False
    if not resume and bc_path:
        value_pretrained = _bc_value_pretrained(bc_path)
        if value_pretrained:
            log("[run_rl] BC value head MC-pretrained - keep value (skip zeroing)")
    if not resume:
        _warm_start_normalize(model, keep_value=value_pretrained)
        save_weights_json(model, rl_path)
    print(
        f"[{time.strftime('%H:%M:%S')}] [run_rl] "
        + ("resume" if resume else "init")
        + f" weights <- {src} "
        f"(params={sum(int(p.numel()) for p in model.parameters())})"
        + ("" if resume else f" -> {rl_path}")
    )
    return model

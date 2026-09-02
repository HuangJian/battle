"""build_model —— RL 权重初始化（BC warm-start / RL resume / A4 归一校准）。

从 run_rl.py 拆出（瘦身，2026-09-02）：build_model 是训练路径首个需要 torch
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

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.log import log
from rl.modes import get_backend

if TYPE_CHECKING:  # build_model 返回注解（future annotations 下不运行时求值）
    import torch


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
    import numpy as np
    import torch

    import ppo.engine as ppo_mod
    import ppo.goal as ppo_goal
    import ppo.intent as ppo_intent
    from data.weights_io import load_state_into, save_weights_json
    from models.student import PPOStudent

    if mode in ("intent", "goal"):
        PPO = get_backend(mode)
        resume = os.path.exists(rl_path)
        if not resume:
            script = "ppo_goal.py" if mode == "goal" else "ppo_intent.py"
            log(f"init RL weights from BC ({bc_path}) -> {rl_path} ({mode} backend)")
            subprocess.run(
                [
                    sys.executable,
                    f"nn-training/{script}",
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

    resume = os.path.exists(rl_path)
    src = rl_path if resume else bc_path
    model = ppo_mod.build_ppo(src)
    load_state_into(model, src)
    if not resume:
        # goal-nn 卡 A4（2026-08-30 最终版）：BC 权重有两个 PPO 不可消费的量级问题——
        # ① BC 训练动态把 ConvMixer trunk 激活放大到真实局面上 ~千级（合成探针会
        #    低估百倍，必须用真实 shard obs 校准）；
        # ② 策略头 logits ±7600 ⇒ 采样近 one-hot、熵≈0.01，PPO 无法探索也无法
        #    消费（kl 一次更新爆 3 万）。
        # 处置（warm_start_normalize，见下）：真实 obs 校准 trunk→h≈15；
        # move/fire 头缩到 logit 范围 ~3（保 argmax、软先验、熵≈1）；value 头清零。
        import numpy as np
        import torch

        def _sample_real_obs(n: int = 16) -> torch.Tensor:
            """真实 obs 校准样本：多个最近 shard 各取一层 + 合成极端（全零/全亮/条纹），
            取并集——单一 shard 可能是退化样本（全暗 obs 曾让 feat_max=1，α 放大 14x
            把已归一的 trunk 再抬爆，2026-08-30 s1-cap 首启实测）。"""
            import glob
            import os

            paths = sorted(
                glob.glob(str(REPO_ROOT / "tmp" / "*" / "it*" / "**" / "obs.npy"), recursive=True),
                key=os.path.getmtime,
                reverse=True,
            )[:8]
            chunks: list[torch.Tensor] = []
            for p_ in paths:
                try:
                    arr = np.load(p_, mmap_mode="r")
                    if arr.ndim == 4 and arr.shape[1] == 14 and arr.shape[0] >= 1:
                        chunks.append(torch.from_numpy(np.ascontiguousarray(arr[:n])))
                except Exception:
                    continue
            synth = torch.zeros(3, 14, 26, 26, dtype=torch.uint8)
            synth[1] = 255
            synth[2, :, ::2] = 255
            chunks.append(synth)
            return torch.cat(chunks, dim=0)

        def warm_start_normalize(model: PPOStudent) -> None:
            TRUNK = ("stem.", "blocks.", "fc.")
            sample = _sample_real_obs(32)
            sc = torch.zeros(sample.shape[0], 19)

            def _feat_max() -> float:
                with torch.no_grad():
                    return float(model.features(sample, sc).abs().max()) + 1e-6

            def _logit_max() -> float:
                with torch.no_grad():
                    mv, fr, _v = model(sample, sc)
                return max(float(mv.abs().max()), float(fr.abs().max())) + 1e-6

            alpha = 15.0 / _feat_max()
            with torch.no_grad():
                for n, p_ in model.named_parameters():
                    if n.startswith(TRUNK):
                        p_.mul_(alpha)
            beta = 3.0 / _logit_max()  # 保 argmax 的软先验：logit 范围 ~3（熵≈1.2）
            with torch.no_grad():
                for n, p_ in model.named_parameters():
                    if n.startswith(("move_head.", "fire_head.")):
                        p_.mul_(beta)
                    elif n.startswith("value_head."):
                        p_.zero_()
            print(
                f"[run_rl] BC warm-start normalize: trunk x{alpha:.4g}, "
                f"policy heads x{beta:.4g} (logit range -> 3.0 soft prior), value zeroed; "
                f"feat_max={15.0 / alpha:.0f}, logit_max_pre={3.0 / beta:.1f}"
            )

        warm_start_normalize(model)
        save_weights_json(model, rl_path)
    print(
        f"[{time.strftime('%H:%M:%S')}] [run_rl] "
        + ("resume" if resume else "init")
        + f" weights <- {src} "
        f"(params={sum(int(p.numel()) for p in model.parameters())})"
        + ("" if resume else f" -> {rl_path}")
    )
    return model



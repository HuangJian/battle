"""Paddle ↔ torch parity harness for the PPO port (PoC, 2026-10-27).

Two interpreters, one file:

  1. torch side (repo venv, has torch):
       bash tools/githook/nn-py-safe.sh tools/paddle_parity.py fixture --out tmp/paddle-parity
  2. Paddle side (isolated venv, has paddle only):
       tmp/paddle-venv/Scripts/python.exe tools/paddle_parity.py check --dir tmp/paddle-parity
  3. back to torch — the Paddle-written weights JSON must serve unchanged:
       bash tools/githook/nn-py-safe.sh tools/paddle_parity.py verify --dir tmp/paddle-parity

The fixture stores, per case: initial weights JSON, the minibatch (+ demo bank),
reference forward logits, one-epoch update stats and post-update weights. `check`
rebuilds the same model from the same weights file, runs the same step and prints
max|Δ| for forward / stats / weights. Nothing here touches production paths.

Cases (one arch × one feature each, kept orthogonal on purpose):
  legacy_plain  — baseline clipped PPO
  legacy_kl_ent — kl_coef + ent_coef override
  legacy_kick   — BC-kickstart ref KL (legacy 5/2)
  legacy_demo   — demo bank move-CE (legacy 7-wide mask)
  extra_anchor  — policy_extra arch + value-anchor pull + policy_new_rms
  v6_kick       — v6 three heads (4/3/2, 9-wide mask) + kickstart ref
  tower         — spatial_tower arch + value-anchor pull
  ckpt_resume   — torch 写 ckpt → Paddle 读/续跑一个 epoch → 写回 torch 格式
                  （`checkpoint_paddle` 的端到端验收；`verify` 再用 torch 读 Paddle 写的那份）

BC (#10, 2026-11-06) 走同一文件的三步对账：
  bc-fixture — torch 造一份恒定 v6 语料 + 初始权重 + 1 epoch 基准
  bc-check   — paddle 用同一初始权重跑 1 epoch，对 metrics / 前向；导出权重
  bc-verify  — torch 读 Paddle 导出的权重 JSON，前向必须对上 Paddle 自己的输出
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.cli import house_parser, run_main
from common.schema import (
    BOARD,
    OBS_CHANNELS,
    POLICY_EXTRA_DIM,
    SCALAR_DIM,
)

SEED = 20261027
B = 8
DEMO_N = 32
LR = 3e-4

#: 续跑（ckpt 载入之后的**第二步**）的容差。第一步的更新被 bias correction 归一化成 ±lr，
#: 梯度差异被完全掩盖；第二步起 m/sqrt(v) 才真的看梯度 —— 实测反向 f32 噪声 ~3.6e-6，
#: 在 v≈0 的极少数坐标上被 Adam 放大到 ~12% lr（72264 个元素里 7 个 > 1e-5）。
#: 轨迹容差因此按量级放宽；「转换本身对不对」由载入段的逐位判据与单元测试守。
CKPT_STATS_TOL = 3e-4
CKPT_AFTER_TOL = 1e-3

#: 一步更新权重的**少量坐标离群**容差（v7 观测通道 16→17 后新增，2026-11-01）。
#:
#: 现象：`legacy_plain` 有 5 个坐标（stem [30,0,1,2] 最大）torch/paddle 一步更新方向相反、
#: 幅度 ≈±lr（-2.81e-4 vs +2.99e-4）——不是公式分叉，而是 Adam 在 v≈0 的坐标上把 f32
#: 正负噪声放大到饱和（同族已知边界见 `CKPT_STATS_TOL` 的模块头：16 通道时 72,264 个元素
#: 里 7 个 >1e-5）。其余 72,835 个坐标平均 |Δ|=5.1e-8；v7 之前（worktree 62ed797a）同套
#: fixture 全 PASS（check worst 4.21e-5 / verify 8.41e-6）。
#:
#: 判据（整案全局，不逐张量豁免）：超过 `atol_weights` 的元素 ≤8 个、最大 ≤2·lr
#: （Adam 单步上界）、均值 ≤`atol_weights`/10。**不**把全局 atol 抬到 2·lr：那样任意
#: 多个坐标都能偏 6e-4；按条数封顶才是「少数 v≈0 坐标」的形状（同族先例 = 上面的 7 个）。
#: 满足时额外允许更新后前向 `atol_after + 2·d_w`（离群坐标的输出后果）。
#: 真实公式分叉会**同时**打整片坐标（远多于 8 个）⇒ 这条豁免盖不住它。
OUTLIER_MAX_ELEMS = 8
OUTLIER_MAX_LR_MULT = 2.0


def _weight_outlier_ok(sorted_abs: np.ndarray, atol: float) -> tuple[bool, int, float]:
    """整案一步权重差的「少量坐标离群」判据（供 `_check` 与单测共用）。

    `sorted_abs` = 全部张量的 |Δ| 升序（一次拼接，不逐张量豁免）。
    返回 `(是否允收, 超 atol 的元素数, 均值)`。
    """
    if sorted_abs.size == 0:
        return True, 0, 0.0
    n_big = int((sorted_abs > atol).sum())
    mean_d = float(sorted_abs.mean())
    ok = (
        n_big <= OUTLIER_MAX_ELEMS
        and float(sorted_abs[-1]) <= OUTLIER_MAX_LR_MULT * LR
        and mean_d <= atol / 10.0
    )
    return ok, n_big, mean_d


ARCHS: dict[str, dict[str, Any]] = {
    "legacy": dict(policy_extra=False, spatial_tower=False, action_v6=False),
    "extra": dict(policy_extra=True, spatial_tower=False, action_v6=False),
    "v6": dict(policy_extra=False, spatial_tower=False, action_v6=True),
    "tower": dict(policy_extra=True, spatial_tower=True, action_v6=False),
}

CASES: list[dict[str, Any]] = [
    dict(name="legacy_plain", arch="legacy"),
    dict(name="legacy_kl_ent", arch="legacy", kl_coef=0.05, ent_coef=0.02),
    dict(name="legacy_kick", arch="legacy", kickstart_kl=0.1, ref=True),
    dict(name="legacy_demo", arch="legacy", demo=True, demo_bc_coef=0.5, demo_per_mb=8),
    dict(name="extra_anchor", arch="extra", value_anchor=0.1),
    dict(name="v6_kick", arch="v6", kickstart_kl=0.1, ref=True),
    dict(name="tower", arch="tower", value_anchor=0.05),
    dict(name="ckpt_resume", arch="legacy", ckpt=True),
]


def _needs_extra(arch: str) -> bool:
    return bool(ARCHS[arch]["policy_extra"])


def make_chunk(rng: np.random.Generator, arch: str) -> dict:
    v6 = ARCHS[arch]["action_v6"]
    obs = rng.integers(0, 256, size=(B, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8)
    scalars = ((rng.random((B, SCALAR_DIM), dtype=np.float32) - 0.5) * 4).astype(np.float32)
    chunk: dict[str, np.ndarray] = {"obs": obs, "scalars": scalars}
    if _needs_extra(arch):
        chunk["extra"] = (
            (rng.random((B, POLICY_EXTRA_DIM), dtype=np.float32) - 0.25) * 1.5
        ).astype(np.float32)
    if v6:
        mask = np.zeros((B, 9), dtype=np.float32)
        a_move = np.zeros((B,), dtype=np.int64)
        a_fire = np.zeros((B,), dtype=np.int64)
        a_hold = np.zeros((B,), dtype=np.int64)
        for i in range(B):
            mv = rng.choice(4, size=2, replace=False)
            fr = rng.choice(3, size=2, replace=False)
            hd = rng.choice(2, size=2, replace=False)
            mask[i, mv] = 1.0
            mask[i, 4 + fr] = 1.0
            mask[i, 7 + hd] = 1.0
            a_move[i] = int(rng.choice(mv))
            a_fire[i] = int(rng.choice(fr))
            a_hold[i] = int(rng.choice(hd))
        chunk["a_hold"] = a_hold
        chunk["lp_hold"] = (-rng.random(B, dtype=np.float32) * 2).astype(np.float32)
    else:
        mask = np.zeros((B, 7), dtype=np.float32)
        a_move = np.zeros((B,), dtype=np.int64)
        a_fire = np.zeros((B,), dtype=np.int64)
        for i in range(B):
            mv = rng.choice(5, size=2, replace=False)
            fr = rng.choice(2, size=2, replace=False)
            mask[i, mv] = 1.0
            mask[i, 5 + fr] = 1.0
            a_move[i] = int(rng.choice(mv))
            a_fire[i] = int(rng.choice(fr))
    chunk["mask"] = mask
    chunk["a_move"] = a_move
    chunk["a_fire"] = a_fire
    # lp_old 先占位；fixture 会用初始模型真实 logp 覆盖（见 _fill_lp_old）。
    chunk["lp_move"] = np.zeros(B, dtype=np.float32)
    chunk["lp_fire"] = np.zeros(B, dtype=np.float32)
    chunk["adv"] = ((rng.random(B, dtype=np.float32) - 0.5) * 2).astype(np.float32)
    chunk["ret"] = ((rng.random(B, dtype=np.float32) - 0.5) * 2).astype(np.float32)
    return chunk


def _fill_lp_old(chunk: dict, outs, arch: str, rng: np.random.Generator) -> None:
    """把 lp_old 设成初始模型真实 logp ± 小扰动：ratio ≈ 1 附近，clip 的三种分支都被走到。"""
    v6 = ARCHS[arch]["action_v6"]
    mask = chunk["mask"]
    mv, fr = outs[0].numpy(), outs[1].numpy()
    hd = outs[2].numpy() if v6 else None
    move_logp = _masked_logsoftmax_np(mv, mask[:, : 4 if v6 else 5])
    fire_logp = _masked_logsoftmax_np(fr, mask[:, 4:7] if v6 else mask[:, 5:7])
    noise = lambda: (rng.random(B, dtype=np.float32) - 0.5) * 0.2  # noqa: E731
    chunk["lp_move"] = (
        move_logp[np.arange(B), chunk["a_move"]].astype(np.float32) + noise()
    ).astype(np.float32)
    chunk["lp_fire"] = (
        fire_logp[np.arange(B), chunk["a_fire"]].astype(np.float32) + noise()
    ).astype(np.float32)
    if v6:
        assert hd is not None
        hold_logp = _masked_logsoftmax_np(hd, mask[:, 7:9])
        chunk["lp_hold"] = (
            hold_logp[np.arange(B), chunk["a_hold"]].astype(np.float32) + noise()
        ).astype(np.float32)


def _masked_logsoftmax_np(logits: np.ndarray, mask: np.ndarray) -> np.ndarray:
    # 声明类型是给 mypy 的（`warn_return_any`）：`ndarray.max` / `.sum` 在 numpy 的 stub 里
    # 返回 `Any`，不给 `z` 挂类型的话它一路 Any 到 `return` ⇒ 「Returning Any」。运行时不变。
    z: np.ndarray = logits.astype(np.float64) + (1.0 - mask.astype(np.float64)) * (-1e9)
    z = z - z.max(axis=-1, keepdims=True)
    z = z - np.log(np.exp(z).sum(axis=-1, keepdims=True))
    return z


def make_demo(rng: np.random.Generator, arch: str) -> dict:
    v6 = ARCHS[arch]["action_v6"]
    demo: dict[str, np.ndarray] = {
        "obs": rng.integers(0, 256, size=(DEMO_N, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
        "scalars": ((rng.random((DEMO_N, SCALAR_DIM), dtype=np.float32) - 0.5) * 4).astype(
            np.float32
        ),
        "actions": np.zeros((DEMO_N, 2), dtype=np.int64),
        "masks": np.zeros((DEMO_N, 9 if v6 else 7), dtype=np.float32),
    }
    if _needs_extra(arch):
        demo["extra"] = (
            (rng.random((DEMO_N, POLICY_EXTRA_DIM), dtype=np.float32) - 0.25) * 1.5
        ).astype(np.float32)
    for i in range(DEMO_N):
        if v6:
            mv = rng.choice(4, size=2, replace=False)
            fr = rng.choice(3, size=2, replace=False)
            hd = rng.choice(2, size=2, replace=False)
            demo["masks"][i, mv] = 1.0
            demo["masks"][i, 4 + fr] = 1.0
            demo["masks"][i, 7 + hd] = 1.0
        else:
            mv = rng.choice(5, size=2, replace=False)
            fr = rng.choice(2, size=2, replace=False)
            demo["masks"][i, mv] = 1.0
            demo["masks"][i, 5 + fr] = 1.0
        demo["actions"][i, 0] = int(rng.choice(mv))
        demo["actions"][i, 1] = int(rng.choice(fr))
    return demo


# ---------------------------------------------------------------- torch side


def run_fixture(out: Path) -> None:
    import torch

    from worker.data.weights_io import load_state_into, save_weights_json
    from worker.models.student import PPOStudent
    from worker.ppo import engine

    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)

    for name, flags in ARCHS.items():
        torch.manual_seed(SEED + len(name))
        m = PPOStudent(h=64, d=8, head_hidden=128, **flags)
        save_weights_json(m, str(out / "models" / f"{name}.json"))
    for name in ("legacy", "v6"):
        torch.manual_seed(SEED + 100 + len(name))
        rm = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[name])
        save_weights_json(rm, str(out / "models" / f"{name}_ref.json"))

    for case in CASES:
        arch = case["arch"]
        cdir = out / "cases" / case["name"]
        cdir.mkdir(parents=True, exist_ok=True)
        chunk = make_chunk(rng, arch)
        demo = make_demo(rng, arch) if case.get("demo") else None
        (cdir / "case.json").write_text(
            json.dumps(
                {
                    "arch": arch,
                    "weights": f"models/{arch}.json",
                    "ref": f"models/{arch}_ref.json" if case.get("ref") else None,
                    "kl_coef": case.get("kl_coef", 0.0),
                    "kickstart_kl": case.get("kickstart_kl", 0.0),
                    "ent_coef": case.get("ent_coef"),
                    "demo_bc_coef": case.get("demo_bc_coef", 0.0),
                    "demo_per_mb": case.get("demo_per_mb", 0),
                    "value_anchor": case.get("value_anchor", 0.0),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        model = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[arch])
        load_state_into(model, str(out / "models" / f"{arch}.json"))
        model.eval()
        with torch.no_grad():
            tens = {k: torch.from_numpy(v) for k, v in chunk.items()}
            outs = model(tens["obs"], tens["scalars"], tens.get("extra"))
        _fill_lp_old(chunk, outs, arch, rng)
        np.savez(cdir / "chunk.npz", **chunk)
        if demo is not None:
            np.savez(cdir / "demo.npz", **demo)
        fwd = {"fwd_move": outs[0].numpy(), "fwd_fire": outs[1].numpy()}
        if ARCHS[arch]["action_v6"]:
            fwd["fwd_hold"] = outs[2].numpy()
            fwd["fwd_value"] = outs[3].numpy()
        else:
            fwd["fwd_value"] = outs[2].numpy()
        np.savez(cdir / "forward.npz", **fwd)

        model.train()
        opt = torch.optim.Adam(model.parameters(), lr=LR)
        ref_model = None
        if case.get("ref"):
            ref_model = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[arch])
            load_state_into(ref_model, str(out / "models" / f"{arch}_ref.json"))
            for p in ref_model.parameters():
                p.requires_grad = False
            ref_model.eval()
        np.random.seed(SEED)
        stats = engine.ppo_update(
            model,
            opt,
            [chunk],
            epochs=1,
            device="cpu",
            kl_coef=case.get("kl_coef", 0.0),
            ref_model=ref_model,
            kickstart_kl=case.get("kickstart_kl", 0.0),
            ent_coef=case.get("ent_coef"),
            demo_bank=demo,
            demo_bc_coef=case.get("demo_bc_coef", 0.0),
            demo_per_mb=case.get("demo_per_mb", 0),
            value_anchor=case.get("value_anchor", 0.0),
        )
        (cdir / "expected.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        save_weights_json(model, str(cdir / "weights_after.json"))
        # 更新后再前向一次：权重本身可以因 Adam 对近零梯度的归一化而逐元素漂移，
        # 真正该守的是「函数」——同一输入下两边的输出是否仍在噪声内。
        model.eval()
        with torch.no_grad():
            out_after = model(tens["obs"], tens["scalars"], tens.get("extra"))
        fwd_after = {"after_move": out_after[0].numpy(), "after_fire": out_after[1].numpy()}
        if ARCHS[arch]["action_v6"]:
            fwd_after["after_hold"] = out_after[2].numpy()
            fwd_after["after_value"] = out_after[3].numpy()
        else:
            fwd_after["after_value"] = out_after[2].numpy()
        np.savez(cdir / "forward_after.npz", **fwd_after)
        if case.get("ckpt"):
            import shutil

            from worker.ppo.common import _ppo_save

            ckpt = cdir / "ckpt_torch"
            _ppo_save(str(ckpt), model, opt, epochs_done=1)
            # 续跑**用副本**：torch 引擎在 epoch 尾会写回 ckpt_path，直接拿 ckpt_torch 续跑
            # 会把「epoch 1 快照」覆盖成 epoch 2 的（check 侧就再也读不到起点）。
            ckpt_run = cdir / "ckpt_torch_run"
            shutil.rmtree(ckpt_run, ignore_errors=True)
            shutil.copytree(ckpt, ckpt_run)
            chunk2 = make_chunk(rng, arch)
            model.eval()
            with torch.no_grad():
                t2 = {k: torch.from_numpy(v) for k, v in chunk2.items()}
                outs2 = model(t2["obs"], t2["scalars"], t2.get("extra"))
            _fill_lp_old(chunk2, outs2, arch, rng)
            np.savez(cdir / "chunk2.npz", **chunk2)
            stats2 = engine.ppo_update(
                model, opt, [chunk2], epochs=2, device="cpu", ckpt_path=str(ckpt_run)
            )
            (cdir / "expected2.json").write_text(json.dumps(stats2, indent=2), encoding="utf-8")
            save_weights_json(model, str(cdir / "weights_after2.json"))
            model.eval()
            with torch.no_grad():
                out2 = model(t2["obs"], t2["scalars"], t2.get("extra"))
            np.savez(
                cdir / "forward_after2.npz",
                after2_move=out2[0].numpy(),
                after2_fire=out2[1].numpy(),
                after2_value=out2[-1].numpy(),
            )
            print(
                f"fixture ckpt: {ckpt.name} + resumed epoch "
                f"stats2={ {k: round(v, 6) for k, v in stats2.items()} }"
            )
        print(f"fixture {case['name']}: stats={ {k: round(v, 6) for k, v in stats.items()} }")


# ---------------------------------------------------------------- paddle side


def _rel(got: np.ndarray, ref: np.ndarray) -> float:
    """max|Δ| / max(1, |ref|max)：logits 可到 ±130，绝对阈值会随量级失真。"""
    return float(np.abs(got - ref).max()) / max(1.0, float(np.abs(ref).max()))


def run_check(
    root: Path,
    atol_forward: float,
    atol_stats: float,
    rtol_stats: float,
    atol_weights: float,
    atol_after: float,
) -> int:
    import paddle

    from worker.models.student_paddle import PPOStudent, load_state_into
    from worker.ppo import engine_paddle

    paddle.set_device("cpu")
    worst = {"fwd": 0.0, "stats": 0.0, "w": 0.0, "after": 0.0}
    failed = 0
    for case in CASES:
        cdir = root / "cases" / case["name"]
        cfg = json.loads((cdir / "case.json").read_text(encoding="utf-8"))
        arch = cfg["arch"]
        model = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[arch])
        load_state_into(model, str(root / cfg["weights"]))
        model.eval()
        chunk = dict(np.load(cdir / "chunk.npz"))
        with paddle.no_grad():
            tens = {k: paddle.to_tensor(v) for k, v in chunk.items()}
            outs = model(tens["obs"], tens["scalars"], tens.get("extra"))
        fwd = np.load(cdir / "forward.npz")
        d_fwd = max(
            _rel(outs[0].numpy(), fwd["fwd_move"]),
            _rel(outs[1].numpy(), fwd["fwd_fire"]),
            _rel(outs[-1].numpy(), fwd["fwd_value"]),
        )
        if ARCHS[arch]["action_v6"]:
            d_fwd = max(d_fwd, _rel(outs[2].numpy(), fwd["fwd_hold"]))

        model.train()
        opt = paddle.optimizer.Adam(learning_rate=LR, parameters=model.parameters())
        ref_model = None
        if cfg["ref"]:
            ref_model = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[arch])
            load_state_into(ref_model, str(root / cfg["ref"]))
            for p in ref_model.parameters():
                p.stop_gradient = True
            ref_model.eval()
        demo = dict(np.load(cdir / "demo.npz")) if (cdir / "demo.npz").exists() else None
        np.random.seed(SEED)
        stats = engine_paddle.ppo_update(
            model,
            opt,
            [chunk],
            epochs=1,
            kl_coef=cfg["kl_coef"],
            ref_model=ref_model,
            kickstart_kl=cfg["kickstart_kl"],
            ent_coef=cfg["ent_coef"],
            demo_bank=demo,
            demo_bc_coef=cfg["demo_bc_coef"],
            demo_per_mb=cfg["demo_per_mb"],
            value_anchor=cfg["value_anchor"],
        )
        expected = json.loads((cdir / "expected.json").read_text(encoding="utf-8"))
        d_stats = max(
            float(abs(stats[k] - expected[k]) / (1.0 + abs(expected[k]))) for k in expected
        )
        exp_w = json.loads((cdir / "weights_after.json").read_text(encoding="utf-8"))["params"]
        after = model.state_dict()
        d_w = 0.0
        worst_key = ""
        all_d: list[np.ndarray] = []
        for k, v in exp_w.items():
            ref = np.frombuffer(base64.b64decode(v["data"]), dtype="<f4").reshape(v["shape"])
            got = after[k].numpy()
            d = np.abs(got - ref)
            all_d.append(d.ravel())
            dm = float(d.max())
            if dm > d_w:
                d_w, worst_key = dm, k
        # 一步更新权重判据：常规 atol，或**少量坐标离群**（见 OUTLIER_MAX_ELEMS 注释）。
        flat = np.sort(np.concatenate(all_d))
        single_outlier, n_big, _mean_d = _weight_outlier_ok(flat, atol_weights)
        weights_ok = d_w <= atol_weights or single_outlier
        # 产物回归：Paddle 训练完的权重必须能被 torch/TS 生态直接读（权重 JSON 是接口）。
        from worker.models.student_paddle import save_weights_json as paddle_save_weights

        paddle_save_weights(model, str(cdir / "paddle_after.json"))
        model.eval()
        with paddle.no_grad():
            out_after = model(tens["obs"], tens["scalars"], tens.get("extra"))
        fwd_after = np.load(cdir / "forward_after.npz")
        d_after = max(
            _rel(out_after[0].numpy(), fwd_after["after_move"]),
            _rel(out_after[1].numpy(), fwd_after["after_fire"]),
            _rel(out_after[-1].numpy(), fwd_after["after_value"]),
        )
        if ARCHS[arch]["action_v6"]:
            d_after = max(d_after, _rel(out_after[2].numpy(), fwd_after["after_hold"]))
        np.savez(
            cdir / "paddle_forward_after.npz",
            after_move=out_after[0].numpy(),
            after_fire=out_after[1].numpy(),
            after_value=out_after[-1].numpy(),
            **({"after_hold": out_after[2].numpy()} if ARCHS[arch]["action_v6"] else {}),
        )
        worst["fwd"] = max(worst["fwd"], d_fwd)
        worst["stats"] = max(worst["stats"], d_stats)
        worst["w"] = max(worst["w"], d_w)
        worst["after"] = max(worst["after"], d_after)
        stats_bad = [
            k
            for k in expected
            if abs(stats[k] - expected[k]) > atol_stats + rtol_stats * abs(expected[k])
        ]
        # 单坐标离群时，更新后前向的差额是那一步权重差的后果 ⇒ 容差随 d_w 放一条窄缝。
        after_ok = d_after <= atol_after or (
            single_outlier and d_after <= atol_after + 2.0 * d_w
        )
        ok = d_fwd <= atol_forward and not stats_bad and weights_ok and after_ok
        failed += 0 if ok else 1
        suffix = ""
        if not ok:
            suffix = f" (worst={worst_key})"
        elif single_outlier and d_w > atol_weights:
            suffix = f" [{n_big}-coord-outlier]"
        print(
            f"{'PASS' if ok else 'FAIL'} {case['name']:<14} "
            f"fwd={d_fwd:.2e} stats={d_stats:.2e} weights={d_w:.2e} after={d_after:.2e}"
            + suffix
        )
        if stats_bad:
            for k in stats_bad:
                print(f"    {k}: torch={expected[k]:.8f} paddle={stats[k]:.8f}")
        if case.get("ckpt"):
            ok_ckpt = _check_ckpt(cdir, arch, atol_weights)
            failed += 0 if ok_ckpt else 1
    print(
        f"\nworst: fwd={worst['fwd']:.2e} stats={worst['stats']:.2e} "
        f"weights={worst['w']:.2e} after={worst['after']:.2e} "
        f"| tol fwd={atol_forward:g} stats={atol_stats:g}+{rtol_stats:g}*x "
        f"weights={atol_weights:g}（or ≤{OUTLIER_MAX_ELEMS}-coord ≤{OUTLIER_MAX_LR_MULT:g}·lr）"
        f" after={atol_after:g}（离群时 +2·d_w）"
    )
    return failed


# ---------------------------------------------------------------- paddle side (2)


def _check_ckpt(cdir: Path, arch: str, atol_weights: float) -> bool:
    """torch 格式 ckpt → Paddle 读入（逐位）→ 续跑一个 epoch → 写回 torch 格式。

    判据分两段：
      ① **载入**：`load_ckpt` 之后的模型权重与 `model.pt` 逐位相同；`opt.state_dict()`
         与 `torch_state_to_paddle(opt.pt)` 逐位相同（这半条盯的是 Paddle
         `set_state_dict` 有没有真把懒创建的累加器认领走，不是转换函数自证）；
      ② **续跑**：同一份 chunk2 + 同一 RNG 状态下再跑一个 epoch，stats/权重/函数输出
         与 torch 自己续跑的结果在 `CKPT_*_TOL` 内相同 —— pow 的指数若错一位，kl 等
         统计量会整体偏 ~18%，这里必红。
    """
    import shutil

    import paddle

    from common.torch_pickle import load_torch_pickle
    from worker.models.student_paddle import PPOStudent
    from worker.ppo import engine_paddle
    from worker.ppo.checkpoint_paddle import load_ckpt, named_params
    from worker.ppo.ckpt_convert import torch_state_to_paddle

    dst = cdir / "ckpt_paddle"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(cdir / "ckpt_torch", dst)

    model = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[arch])
    opt = paddle.optimizer.Adam(learning_rate=LR, parameters=model.parameters())
    epochs_done, steps_done = load_ckpt(model, opt, str(dst))
    assert (epochs_done, steps_done) == (1, 1), (
        f"load_ckpt 返回 ({epochs_done}, {steps_done})，期望 (1, 1)"
    )
    mstate = load_torch_pickle((dst / "model.pt").read_bytes())
    sd = model.state_dict()
    d_load = max(
        float(np.abs(sd[k].numpy() - np.asarray(v)).max()) for k, v in mstate.items()
    )
    ostate = load_torch_pickle((dst / "opt.pt").read_bytes())
    want, want_steps = torch_state_to_paddle(ostate, named_params(model))
    flat = {k: v.numpy() for k, v in opt.state_dict().items()}
    d_opt = max(float(np.abs(flat[k] - v).max()) for k, v in want.items())
    assert want_steps == steps_done

    chunk2 = dict(np.load(cdir / "chunk2.npz"))
    stats2 = engine_paddle.ppo_update(
        model, opt, [chunk2], epochs=2, ckpt_path=str(dst)
    )
    expected2 = json.loads((cdir / "expected2.json").read_text(encoding="utf-8"))
    d_stats2 = max(
        float(abs(stats2[k] - expected2[k]) / (1.0 + abs(expected2[k]))) for k in expected2
    )
    exp_w = json.loads((cdir / "weights_after2.json").read_text(encoding="utf-8"))["params"]
    after2 = model.state_dict()
    d_w2 = 0.0
    for k, v in exp_w.items():
        ref = np.frombuffer(base64.b64decode(v["data"]), dtype="<f4").reshape(v["shape"])
        d_w2 = max(d_w2, float(np.abs(after2[k].numpy() - ref).max()))
    model.eval()
    with paddle.no_grad():
        t2 = {k: paddle.to_tensor(v) for k, v in chunk2.items()}
        out2 = model(t2["obs"], t2["scalars"], t2.get("extra"))
    fwd2 = np.load(cdir / "forward_after2.npz")
    d_after2 = max(
        _rel(out2[0].numpy(), fwd2["after2_move"]),
        _rel(out2[1].numpy(), fwd2["after2_fire"]),
        _rel(out2[-1].numpy(), fwd2["after2_value"]),
    )
    stats_bad = [
        k
        for k in expected2
        if abs(stats2[k] - expected2[k]) > CKPT_STATS_TOL * (1.0 + abs(expected2[k]))
    ]
    ok = (
        d_load == 0.0
        and d_opt == 0.0
        and not stats_bad
        and d_w2 <= atol_weights
        and d_after2 <= CKPT_AFTER_TOL
    )
    # Paddle 写出的权重与前向留给 `verify`（torch 直接读 ckpt + 权重 JSON 对函数对账）：
    # 训练轨迹的 f32 噪声不该掩盖「产物本身能不能被 torch 消费」这条独立判据。
    from worker.models.student_paddle import save_weights_json as paddle_save_weights

    paddle_save_weights(model, str(cdir / "paddle_after2.json"))
    np.savez(
        cdir / "paddle_forward_after2.npz",
        after2_move=out2[0].numpy(),
        after2_fire=out2[1].numpy(),
        after2_value=out2[-1].numpy(),
    )
    print(
        f"{'PASS' if ok else 'FAIL'} {'ckpt_resume':<14} "
        f"load_model={d_load:.1e} load_opt={d_opt:.1e} "
        f"stats2={d_stats2:.2e} weights2={d_w2:.2e} after2={d_after2:.2e}"
    )
    for k in stats_bad:
        print(f"    {k}: torch2={expected2[k]:.8f} paddle2={stats2[k]:.8f}")
    return ok


# ---------------------------------------------------------------- torch side (2)


def run_verify(root: Path, atol: float, rtol: float) -> int:
    """torch 读 Paddle 写出的权重 JSON，前向必须对上 Paddle 的更新后输出。

    判据 = `max|Δ| ≤ atol + rtol·max(1,|ref|max)`（与 `--atol-stats` 同族的绝对+相对缝）：
    `_rel` 只按 ref 量级归一，量级小的头（value ≈ 0.7）会把 f32 末位噪声放大成 2e-5
    （v7 的 stem 19 通道改变累加长度后实测 2.29e-5；16 通道时同例 8.41e-6）。
    绝对项 1e-5 + 相对项 1e-4·scale 给这一类小量级头留出与 torch↔TS golden 同口径的缝，
    而大 logits（±600）仍按 1e-4 相对判、不会变松。
    """
    import torch

    from worker.data.weights_io import load_state_into
    from worker.models.student import PPOStudent

    failed = 0
    worst = 0.0
    for case in CASES:
        cdir = root / "cases" / case["name"]
        arch = ARCHS[case["arch"]]
        model = PPOStudent(h=64, d=8, head_hidden=128, **arch)
        pj = cdir / "paddle_after.json"
        load_state_into(model, str(pj))
        chunk = dict(np.load(cdir / "chunk.npz"))
        with torch.no_grad():
            t = {k: torch.from_numpy(v) for k, v in chunk.items()}
            outs = model(t["obs"], t["scalars"], t.get("extra"))
        ref = np.load(cdir / "paddle_forward_after.npz")
        heads: list[tuple[str, int]] = [
            ("after_move", 0),
            ("after_fire", 1),
            ("after_value", -1),
        ]
        if arch["action_v6"]:
            heads.append(("after_hold", 2))
        d = 0.0
        ok = True
        for key, i in heads:
            r = ref[key]
            got = outs[i].numpy()
            absd = float(np.abs(got - r).max())
            scale = max(1.0, float(np.abs(r).max()))
            d = max(d, absd / scale)
            ok = ok and absd <= atol + rtol * scale
        worst = max(worst, d)
        failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {case['name']:<14} paddle→torch weights fwd_rel={d:.2e}")
        if case.get("ckpt"):
            failed += 0 if _verify_ckpt(cdir, case["arch"], atol) else 1
    print(f"\nworst paddle→torch = {worst:.2e} | tol {atol:g}+{rtol:g}·scale")
    return failed


def _verify_ckpt(cdir: Path, arch: str, atol: float) -> bool:
    """torch 直接读 Paddle 写出的 model.pt / opt.pt（`weights_only=True` 默认口径）。

    验收点：两件套能无警告读入、`step`/`lr` 没走样、读出的权重就是 Paddle 续跑后
    那份权重 JSON、同一输入的前向函数对得上 Paddle 自己的输出 —— 「Paddle 写、torch
    接着练」不靠任何旁路。对照物是 Paddle 的产物，不是 torch 的期望值：续跑轨迹的
    f32 噪声已在 `_check_ckpt` 里按噪声容差判过，这里只问「产物能不能被 torch 消费」。
    """
    import torch

    from worker.models.student import PPOStudent

    ck = cdir / "ckpt_paddle"
    model = PPOStudent(h=64, d=8, head_hidden=128, **ARCHS[arch])
    opt = torch.optim.Adam(model.parameters(), lr=1.0)
    model.load_state_dict(torch.load(ck / "model.pt", map_location="cpu", weights_only=True))
    opt.load_state_dict(torch.load(ck / "opt.pt", map_location="cpu", weights_only=True))
    steps = {int(float(s["step"])) for s in opt.state.values()}
    lr = float(opt.param_groups[0]["lr"])
    exp = json.loads((cdir / "paddle_after2.json").read_text(encoding="utf-8"))["params"]
    d_w = 0.0
    sd = model.state_dict()
    for k, v in exp.items():
        ref = np.frombuffer(base64.b64decode(v["data"]), dtype="<f4").reshape(v["shape"])
        d_w = max(d_w, float(np.abs(sd[k].numpy() - ref).max()))
    chunk2 = dict(np.load(cdir / "chunk2.npz"))
    model.eval()
    with torch.no_grad():
        t2 = {k: torch.from_numpy(v) for k, v in chunk2.items()}
        out2 = model(t2["obs"], t2["scalars"], t2.get("extra"))
    fwd2 = np.load(cdir / "paddle_forward_after2.npz")
    d_fwd = max(
        _rel(out2[0].numpy(), fwd2["after2_move"]),
        _rel(out2[1].numpy(), fwd2["after2_fire"]),
        _rel(out2[-1].numpy(), fwd2["after2_value"]),
    )
    ok = steps == {2} and lr == LR and d_w <= atol and d_fwd <= atol
    print(
        f"{'PASS' if ok else 'FAIL'} {'ckpt_resume':<14} paddle ckpt→torch "
        f"step={sorted(steps)} lr={lr:g} weights={d_w:.2e} fwd={d_fwd:.2e}"
    )
    return ok


def _bc_args(root: Path, tag: str) -> Any:
    """BC 对账的固定参数面（torch/paddle 必须逐项相同）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        data_dir=str(root / "bc-data"),
        arch="student",
        out=str(root / tag / "weights.json"),
        notes="bc-parity",
        resume=str(root / "bc-init.json"),
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=1,
        batch=16,
        lr=3e-3,
        val_split=0.25,
        mirror_p=0.0,
        seed=SEED,
        num_workers=0,
        device="cpu",
        value_coef=0.0,
        fire_pos_weight=0.0,
        weight_exponent=1.0,
        patience=0,
        action_v6=True,
        policy_extra=True,
    )


def _bc_write_data(root: Path) -> Path:
    """恒定 v6 shard：所有样本同分布 ⇒ split/乱序不会改变两边的指标。"""
    d = root / "bc-data" / "bc_s0_seed1"
    d.mkdir(parents=True, exist_ok=True)
    n = 16
    np.save(d / "obs.npy", np.zeros((n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", np.zeros((n, SCALAR_DIM), dtype=np.float32))
    np.save(d / "extra.npy", np.zeros((n, POLICY_EXTRA_DIM), dtype=np.float32))
    for key in ("a_move", "a_fire", "a_hold"):
        np.save(d / f"{key}.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 9), dtype=np.float32))
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    return d


def _bc_load_batch(root: Path) -> dict[str, np.ndarray]:
    return {k: np.load(root / f"bc-batch-{k}.npy") for k in ("obs", "scalars", "extra")}


def _bc_forward_torch(model, root: Path, out_name: str) -> dict[str, np.ndarray]:
    import torch

    data = _bc_load_batch(root)
    with torch.no_grad():
        obs = torch.from_numpy(data["obs"])
        sc = torch.from_numpy(data["scalars"])
        ex = torch.from_numpy(data["extra"])
        out = model(obs, sc, ex)
    arr = {"move": out[0].numpy(), "fire": out[1].numpy(), "hold": out[2].numpy(), "value": out[3].numpy()}
    np.savez(root / out_name, **arr)
    return arr


def bc_fixture(root: Path) -> None:
    """torch 侧：造 v6 语料、初始权重、1 epoch 基准与更新后前向。"""
    import torch

    from worker.data.weights_io import load_state_into, save_weights_json
    from worker.models.student import PPOStudent
    from worker.train.bc import train as torch_bc_train

    root.mkdir(parents=True, exist_ok=True)
    _bc_write_data(root)
    torch.manual_seed(SEED)
    model = PPOStudent(h=64, d=8, head_hidden=128, action_v6=True, policy_extra=True)
    save_weights_json(model, str(root / "bc-init.json"))
    load_state_into(model, str(root / "bc-init.json"))
    model.eval()
    for key, arr in (
        ("obs", np.zeros((16, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8)),
        ("scalars", np.zeros((16, SCALAR_DIM), dtype=np.float32)),
        ("extra", np.zeros((16, POLICY_EXTRA_DIM), dtype=np.float32)),
    ):
        np.save(root / f"bc-batch-{key}.npy", arr)
    _bc_forward_torch(model, root, "bc-torch-before.npz")
    metrics = torch_bc_train(_bc_args(root, "bc-torch"))
    (root / "bc-torch-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    # train() 内部自建模型；基准前向必须从导出的 active 权重重建，不能复用闭包外的初始模型。
    trained = PPOStudent(h=64, d=8, head_hidden=128, action_v6=True, policy_extra=True)
    load_state_into(trained, str(root / "bc-torch" / "weights.json"))
    trained.eval()
    _bc_forward_torch(trained, root, "bc-torch-after.npz")
    shown = {k: metrics["history"][k] for k in ("train_loss", "val_loss", "move_acc", "fire_acc", "hold_acc")}
    print(f"bc-fixture: metrics={shown}")


def bc_check(root: Path, atol_metrics: float, atol_forward: float) -> int:
    """paddle 侧：同一初始权重跑 1 epoch，对 metrics / 前向；写出权重与 Paddle 前向。"""
    import numpy as np
    import paddle

    from worker.models.student_paddle import PPOStudent, load_state_into, save_weights_json
    from worker.train import bc_paddle

    paddle.set_device("cpu")
    model = PPOStudent(h=64, d=8, head_hidden=128, action_v6=True, policy_extra=True)
    load_state_into(model, str(root / "bc-init.json"))
    model.eval()
    batch = _bc_load_batch(root)
    with paddle.no_grad():
        out = model(
            paddle.to_tensor(batch["obs"]),
            paddle.to_tensor(batch["scalars"]),
            paddle.to_tensor(batch["extra"]),
        )
    ref_before = np.load(root / "bc-torch-before.npz")
    d_before = max(
        _rel(out[0].numpy(), ref_before["move"]),
        _rel(out[1].numpy(), ref_before["fire"]),
        _rel(out[2].numpy(), ref_before["hold"]),
        _rel(out[3].numpy(), ref_before["value"]),
    )
    metrics = bc_paddle.train(_bc_args(root, "bc-paddle"))
    ref = json.loads((root / "bc-torch-metrics.json").read_text(encoding="utf-8"))
    keys = ("train_loss", "val_loss", "move_acc", "fire_acc", "hold_acc")
    d_metrics = max(
        abs(float(metrics["history"][k][-1]) - float(ref["history"][k][-1]))
        / (1.0 + abs(float(ref["history"][k][-1])))
        for k in keys
    )
    model = PPOStudent(h=64, d=8, head_hidden=128, action_v6=True, policy_extra=True)
    load_state_into(model, str(root / "bc-paddle" / "weights.json"))
    model.eval()
    with paddle.no_grad():
        out = model(
            paddle.to_tensor(batch["obs"]),
            paddle.to_tensor(batch["scalars"]),
            paddle.to_tensor(batch["extra"]),
        )
    ref_after = np.load(root / "bc-torch-after.npz")
    d_after = max(
        _rel(out[0].numpy(), ref_after["move"]),
        _rel(out[1].numpy(), ref_after["fire"]),
        _rel(out[2].numpy(), ref_after["hold"]),
        _rel(out[3].numpy(), ref_after["value"]),
    )
    np.savez(
        root / "bc-paddle-forward.npz",
        move=out[0].numpy(),
        fire=out[1].numpy(),
        hold=out[2].numpy(),
        value=out[3].numpy(),
    )
    save_weights_json(model, str(root / "bc-paddle-weights.json"))
    ok = d_before <= atol_forward and d_metrics <= atol_metrics and d_after <= atol_forward
    print(
        f"{'PASS' if ok else 'FAIL'} bc_forward={d_before:.2e} "
        f"bc_metrics={d_metrics:.2e} bc_after={d_after:.2e} "
        f"| tol forward={atol_forward:g} metrics={atol_metrics:g}"
    )
    return 0 if ok else 1


def bc_verify(root: Path, atol: float) -> int:
    """torch 侧：读 Paddle 导出的权重 JSON，前向必须对上 Paddle 自己的输出。"""
    import numpy as np
    import torch

    from worker.data.weights_io import load_state_into
    from worker.models.student import PPOStudent

    model = PPOStudent(h=64, d=8, head_hidden=128, action_v6=True, policy_extra=True)
    load_state_into(model, str(root / "bc-paddle-weights.json"))
    batch = _bc_load_batch(root)
    with torch.no_grad():
        out = model(
            torch.from_numpy(batch["obs"]),
            torch.from_numpy(batch["scalars"]),
            torch.from_numpy(batch["extra"]),
        )
    ref = np.load(root / "bc-paddle-forward.npz")
    d = max(
        _rel(out[0].numpy(), ref["move"]),
        _rel(out[1].numpy(), ref["fire"]),
        _rel(out[2].numpy(), ref["hold"]),
        _rel(out[3].numpy(), ref["value"]),
    )
    ok = d <= atol
    print(f"{'PASS' if ok else 'FAIL'} bc_paddle→torch fwd_rel={d:.2e} | tol {atol:g}")
    return 0 if ok else 1


def main() -> int:
    ap = house_parser(description="Paddle ↔ torch PPO/BC parity harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("fixture", help="torch: 生成基准（权重/输入/前向/一步更新结果）")
    p1.add_argument("--out", default="tmp/paddle-parity")
    p2 = sub.add_parser("check", help="paddle: 复跑并对比")
    p2.add_argument("--dir", default="tmp/paddle-parity")
    p2.add_argument("--atol-forward", type=float, default=1e-5)
    p2.add_argument("--atol-stats", type=float, default=1e-5)
    p2.add_argument("--rtol-stats", type=float, default=1e-4)
    # Adam 会把近零梯度归一化到同一量级 ⇒ 逐元素权重差的上界就是 lr 的分数；
    # 1e-4 = 单步 lr(3e-4) 的 1/3，函数层面的对账由 --atol-after 守。
    p2.add_argument("--atol-weights", type=float, default=1e-4)
    p2.add_argument("--atol-after", type=float, default=1e-4)
    p3 = sub.add_parser("verify", help="torch: 读 Paddle 写出的权重 JSON 并前向对账")
    p3.add_argument("--dir", default="tmp/paddle-parity")
    p3.add_argument("--atol", type=float, default=1e-5)
    # 小量级头（value ≈0.7）的 f32 末位噪声：绝对项 + 相对项（理由见 run_verify 文档）。
    p3.add_argument("--rtol", type=float, default=1e-4)
    p4 = sub.add_parser("bc-fixture", help="torch: 生成 BC v6 一步基准")
    p4.add_argument("--dir", default="tmp/bc-paddle-parity")
    p5 = sub.add_parser("bc-check", help="paddle: 复跑 BC 一步并对账")
    p5.add_argument("--dir", default="tmp/bc-paddle-parity")
    p5.add_argument("--atol-metrics", type=float, default=1e-3)
    p5.add_argument("--atol-forward", type=float, default=1e-4)
    p6 = sub.add_parser("bc-verify", help="torch: 读 Paddle BC 权重并前向对账")
    p6.add_argument("--dir", default="tmp/bc-paddle-parity")
    p6.add_argument("--atol", type=float, default=1e-4)
    args = ap.parse_args()
    if args.cmd == "fixture":
        run_fixture(Path(args.out))
        return 0
    if args.cmd == "verify":
        return 1 if run_verify(Path(args.dir), args.atol, args.rtol) else 0
    if args.cmd == "bc-fixture":
        bc_fixture(Path(args.dir))
        return 0
    if args.cmd == "bc-check":
        return bc_check(Path(args.dir), args.atol_metrics, args.atol_forward)
    if args.cmd == "bc-verify":
        return bc_verify(Path(args.dir), args.atol)
    return (
        1
        if run_check(
            Path(args.dir),
            args.atol_forward,
            args.atol_stats,
            args.rtol_stats,
            args.atol_weights,
            args.atol_after,
        )
        else 0
    )


if __name__ == "__main__":
    run_main(main)

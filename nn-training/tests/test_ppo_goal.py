"""test_ppo_goal.py — ppo_goal.py 常驻回归测试（无真实训练、不碰节点）。

覆盖（T7.2 验收 + T9a.1b 采样一致性）：
  1) Δt≡1 退化：compute_gae_variable(dt=1) ≡ ppo.compute_gae（定长 γ）。
  2) 变步长 GAE：dt 变化时结果与定长不同。
  3) GoalRLNet export/load 往返 + goal(676)/engage(2)/value(1) 头 shape 断言。
  4) coarse 块聚合：块 logit = 块内 logsumexp（形状/单调/可导）。
  5) policy_logprobs 双动作空间（fine 676 / coarse 169）与掩码零概率。
  6) ppo_update_goal 微型 shard 冒烟（fine+coarse）：不崩、loss 有限、早停、warmup 冻结。

运行（经统一启动器）：
  ./start-training.sh --script test_ppo_goal.py
退出码：全部通过 0，否则 1。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ppo  # noqa: E402
import ppo_goal  # noqa: E402
from goal_net import export_goal_weights  # noqa: E402
from weights_io import load_weights_json  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok  {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILS.append(msg)


def test_dt1_degradation() -> None:
    rng = np.random.RandomState(3)
    N = 64
    reward = rng.randn(N).astype(np.float32)
    value = rng.randn(N).astype(np.float32)
    done = np.zeros(N, dtype=np.int64)
    done[-1] = 1
    dt = np.ones(N, dtype=np.int64)
    a1, r1 = ppo_goal.compute_gae_variable(reward, value, done, dt)
    a2, r2 = ppo.compute_gae(reward, value, done, 0.995, 0.95)
    check(np.array_equal(a1, a2), "dt≡1: adv ≡ ppo.compute_gae 定长路径")
    check(np.array_equal(r1, r2), "dt≡1: ret ≡ ppo.compute_gae 定长路径")


def test_variable_dt_differs() -> None:
    rng = np.random.RandomState(4)
    N = 64
    reward = rng.randn(N).astype(np.float32)
    value = rng.randn(N).astype(np.float32)
    done = np.zeros(N, dtype=np.int64)
    done[-1] = 1
    dt1 = np.ones(N, dtype=np.int64)
    dt2 = rng.randint(60, 241, size=N).astype(np.int64)
    a1, _ = ppo_goal.compute_gae_variable(reward, value, done, dt1)
    a2, _ = ppo_goal.compute_gae_variable(reward, value, done, dt2)
    check(not np.array_equal(a1, a2), "变步长: dt∈[60,240] 与 dt≡1 的 adv 不同")


def test_goal_net_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "goal.json")
        m = ppo_goal.GoalRLNet(h=32, d=2)
        export_goal_weights(m, path)
        meta, _ = load_weights_json(path)
        check(meta["arch"]["kind"] == "goal", "arch.kind == goal")
        shapes = {k: list(v["shape"]) for k, v in meta["params"].items()}
        check(shapes["goal_conv.weight"] == [1, 32, 1, 1], "goal_conv.weight [1,h,1,1]")
        check(shapes["goal_conv.bias"] == [1], "goal_conv.bias [1]")
        check(shapes["engage_head.weight"] == [2, 128 + 9], "engage_head [2,137]")
        check(shapes["value_head.weight"] == [1, 128 + 9], "value_head [1,137]")
        check(not any(k.startswith("intent_head") for k in shapes), "无 intent 头（goal 专属）")
        m2 = ppo_goal.GoalRLNet(h=32, d=2)
        ppo_goal.load_goal_weights(m2, path)
        for k in ("goal_conv.weight", "engage_head.weight", "value_head.weight"):
            check(
                torch.equal(
                    dict(m.named_parameters())[k].data, dict(m2.named_parameters())[k].data
                ),
                f"roundtrip {k}",
            )


def test_coarse_logsumexp() -> None:
    torch.manual_seed(5)
    g = torch.randn(2, 676, requires_grad=True)
    coarse = ppo_goal.coarse_block_logsumexp(g)
    check(tuple(coarse.shape) == (2, 169), "coarse shape (B,169)")
    # 单调：块内最大格增大 ⇒ 块 logit 增大
    g2 = g.detach().clone()
    g2[0, 0] += 10.0  # cell (0,0) ∈ block 0
    coarse2 = ppo_goal.coarse_block_logsumexp(g2)
    check(coarse2[0, 0] > coarse.detach()[0, 0], "块 logit 随块内格单调")
    # 数值：block0 的 4 格 = flat {0,1,26,27}（r=br*2+dr, c=bc*2+dc）
    idx = torch.tensor([0, 1, 26, 27])
    expect = torch.logsumexp(g.detach()[0, idx], dim=0)
    check(torch.allclose(coarse.detach()[0, 0], expect, atol=1e-5), "block0 = logsumexp(cells)")
    # 可导
    coarse.sum().backward()
    check(g.grad is not None and torch.isfinite(g.grad).all(), "logsumexp 可导且梯度有限")


def _fake_chunks(coarse: bool):
    rng = np.random.RandomState(11)
    torch.manual_seed(11)
    N = 64
    dim = ppo_goal.COARSE_DIM if coarse else ppo_goal.FINE_DIM
    obs = rng.randint(0, 256, (N, 14, 26, 26)).astype(np.uint8)
    scalars = rng.randn(N, 19).astype(np.float32) * 0.5
    inject = np.zeros((N, 9), dtype=np.float32)
    inject[:, 0] = 12 / 26
    inject[:, 1] = 9 / 26
    inject[:, 2] = 0.4
    a = rng.randint(0, dim // 2, size=N).astype(np.int64)  # 只采可学（未 mask）动作
    lp = np.full(N, -4.0, dtype=np.float32)
    value = rng.randn(N).astype(np.float32) * 0.1
    reward = rng.randn(N).astype(np.float32) * 0.2
    done = np.zeros(N, dtype=np.int64)
    done[-1] = 1
    mask = np.ones((N, dim), dtype=np.uint8)
    mask[:, dim // 2 :] = 0  # 一半动作不可达
    dt = rng.randint(60, 241, size=N).astype(np.int64)
    engage = rng.randint(0, 2, size=N).astype(np.int64)
    ep = {
        "obs": obs,
        "scalars": scalars,
        "inject": inject,
        "a_goal": a,
        "lp_goal": lp,
        "value": value,
        "reward": reward,
        "done": done,
        "goal_mask": mask,
        "dt": dt,
        "engage": engage,
    }
    adv, ret = ppo_goal.compute_gae_variable(reward, value, done, dt)
    ep["adv"] = (adv - adv.mean()) / (adv.std() + 1e-8)
    ep["ret"] = ret
    return ppo_goal.chunk_episodes([ep], 32)


def test_policy_logprobs_dims() -> None:
    model = ppo_goal.GoalRLNet(h=32, d=2)
    obs = torch.randint(0, 256, (4, 14, 26, 26), dtype=torch.uint8)
    sc = torch.randn(4, 19)
    inj = torch.rand(4, 9)
    lp_f, eng, val = ppo_goal.policy_logprobs(model, obs, sc, inj, torch.ones(4, ppo_goal.FINE_DIM))
    check(tuple(lp_f.shape) == (4, 676), "fine logp (4,676)")
    check(tuple(eng.shape) == (4, 2) and tuple(val.shape) == (4, 1), "engage(4,2)/value(4,1)")
    lp_c, _, _ = ppo_goal.policy_logprobs(model, obs, sc, inj, torch.ones(4, ppo_goal.COARSE_DIM))
    check(tuple(lp_c.shape) == (4, 169), "coarse logp (4,169)")
    # 掩码零概率：被 mask 的动作 logp = -inf（softmax 后 0）
    m = torch.zeros(4, ppo_goal.FINE_DIM)
    m[:, 0] = 1
    lp_m, _, _ = ppo_goal.policy_logprobs(model, obs, sc, inj, m)
    # masked_logsoftmax 用 −1e9 屏蔽（非字面 −inf）⇒ logp < −1e8 ⇒ 采样概率 ≈ 0
    check(bool((lp_m[:, 1:] < -1e8).all()), "被 mask 动作 logp < −1e8（采样概率恒 0）")
    check(bool((lp_m[:, :1] > -1e6).all()), "有效动作 logp 正常量级")


def test_ppo_update_smoke() -> None:
    for coarse in (False, True):
        chunks = _fake_chunks(coarse)
        model = ppo_goal.GoalRLNet(h=32, d=2)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        agg = ppo_goal.ppo_update_goal(
            model, opt, chunks, epochs=3, device=torch.device("cpu"), target_kl=1e9, seed=1
        )
        check(
            np.isfinite(agg["policy"]) and np.isfinite(agg["value"]),
            f"coarse={coarse}: update finite (policy={agg['policy']:.3f} value={agg['value']:.3f})",
        )
        check(not agg["early_stopped"], f"coarse={coarse}: target_kl=1e9 不触发早停")

        model2 = ppo_goal.GoalRLNet(h=32, d=2)
        opt2 = torch.optim.Adam(model2.parameters(), lr=1e-3)
        agg2 = ppo_goal.ppo_update_goal(
            model2, opt2, chunks, epochs=3, device=torch.device("cpu"), target_kl=1e-9, seed=1
        )
        check(agg2["early_stopped"], f"coarse={coarse}: target_kl 极小 → early stop")

        # value warmup：策略头零梯度、主干冻结、value 头被训练。
        model3 = ppo_goal.GoalRLNet(h=32, d=2)
        opt3 = torch.optim.Adam(model3.parameters(), lr=1e-3)
        stem_before = model3.stem.weight.detach().clone()
        goal_before = model3.goal_conv.weight.detach().clone()
        vhead_before = model3.value_head.weight.detach().clone()
        agg3 = ppo_goal.ppo_update_goal(
            model3,
            opt3,
            chunks,
            epochs=2,
            device=torch.device("cpu"),
            target_kl=1e9,
            seed=1,
            value_warmup_epochs=2,
        )
        check(agg3["kl"] == 0.0, f"coarse={coarse}: warmup kl=0（策略冻结）")
        check(torch.equal(goal_before, model3.goal_conv.weight), "warmup: goal_conv 权重不变")
        check(torch.equal(stem_before, model3.stem.weight), "warmup: stem 冻结")
        check(not torch.equal(vhead_before, model3.value_head.weight), "warmup: value_head 已更新")
        check(np.isfinite(agg3["value"]) and agg3["value"] > 0, "warmup: value 头在学")


def main() -> None:
    print("== test_ppo_goal ==")
    test_dt1_degradation()
    test_variable_dt_differs()
    test_goal_net_roundtrip()
    test_coarse_logsumexp()
    test_policy_logprobs_dims()
    test_ppo_update_smoke()
    print(f"== {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failures) ==")
    for m in FAILS:
        print("  -", m)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

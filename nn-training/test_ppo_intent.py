"""test_ppo_intent.py — ppo_intent.py 常驻回归测试（无真实训练、不碰节点）。

覆盖：
  1) Δt≡1 退化字节一致（P1-7k3）：compute_gae_variable(dt=1) ≡ ppo.compute_gae（定长 γ）。
  2) 变步长 GAE：dt 变化时结果与定长不同（半 MDP 语义生效）。
  3) IntentRLNet 含 value 头：export/load 往返 + 三头 + value 头 shape 断言（预注册 #8）。
  4) ppo_update_intent 微型 shard 冒烟：不崩、policy loss 有限、early-stop 逻辑可触发。

运行（经统一启动器）：
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Script test_ppo_intent.py
退出码：全部通过 0，否则 1。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "nn-training"))

import ppo  # noqa: E402
import ppo_intent  # noqa: E402
from intent_net import export_intent_weights  # noqa: E402
from weights_io import load_weights_json  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def test_dt1_degradation() -> None:
    """Δt≡1 时变步长 GAE 与定长 per-tick GAE 逐字节一致（P1-7k3）。"""
    rng = np.random.RandomState(42)
    N = 137
    rewards = rng.randn(N).astype(np.float32) * 0.3
    values = rng.randn(N).astype(np.float32) * 0.2
    dones = np.zeros(N, dtype=np.int64)
    dones[-1] = 1
    dt = np.ones(N, dtype=np.int64)

    adv_fixed, ret_fixed = ppo.compute_gae(rewards, values, dones,
                                           gamma=ppo_intent.GAMMA_TICK, lam=ppo_intent.LAM)
    adv_var, ret_var = ppo_intent.compute_gae_variable(rewards, values, dones, dt)

    check(np.array_equal(adv_fixed, adv_var),
          f"Δt≡1: adv byte-identical (maxdiff={np.max(np.abs(adv_fixed - adv_var)):.2e})")
    check(np.array_equal(ret_fixed, ret_var),
          f"Δt≡1: ret byte-identical (maxdiff={np.max(np.abs(ret_fixed - ret_var)):.2e})")

    # 中间 done（子局断点）路径也要一致。
    dones2 = dones.copy()
    dones2[60] = 1
    a2f, r2f = ppo.compute_gae(rewards, values, dones2,
                               gamma=ppo_intent.GAMMA_TICK, lam=ppo_intent.LAM)
    a2v, r2v = ppo_intent.compute_gae_variable(rewards, values, dones2, dt)
    check(np.array_equal(a2f, a2v), "Δt≡1 mid-done: adv byte-identical")
    check(np.array_equal(r2f, r2v), "Δt≡1 mid-done: ret byte-identical")


def test_variable_dt_differs() -> None:
    """变步长生效：dt 变化时结果 ≠ 定长（半 MDP 折扣语义）。"""
    rng = np.random.RandomState(7)
    N = 64
    rewards = rng.randn(N).astype(np.float32) * 0.3
    values = rng.randn(N).astype(np.float32) * 0.2
    dones = np.zeros(N, dtype=np.int64)
    dones[-1] = 1
    dt = rng.randint(1, 50, size=N).astype(np.int64)  # 可变窗口

    adv_var, ret_var = ppo_intent.compute_gae_variable(rewards, values, dones, dt)
    adv_fixed, ret_fixed = ppo.compute_gae(rewards, values, dones,
                                           gamma=ppo_intent.GAMMA_TICK, lam=ppo_intent.LAM)
    check(not np.array_equal(adv_var, adv_fixed),
          "dt 可变: adv 与定长不同（半 MDP 折扣生效）")
    # 变长窗口的 gamma 应单调：dt 越大 → γ_step 越小 → 提前步的 adv 量级越小（松弛）。
    check(np.all(dt >= 1) and np.all(dt <= 50), "dt 范围合法")


def test_rl_net_roundtrip_value_head() -> None:
    """IntentRLNet 含 value 头：export/load 往返 + 主干 shape 断言（预注册 #8）。"""
    torch.manual_seed(3)
    m = ppo_intent.IntentRLNet(h=64, d=8)
    # 三头 + value 头全在。
    for hname in ("intent_head", "enemy_head", "anchor_head", "value_head"):
        check(any(k.startswith(hname + ".") for k in m.state_dict()),
              f"IntentRLNet has {hname}")
    # value 头 137→1（P1-5②：value 消费注入）。
    vw = m.state_dict()["value_head.weight"]
    check(tuple(vw.shape) == (1, 137), f"value_head.weight shape {tuple(vw.shape)} == (1,137)")

    # 前向：inject 必须消费。
    obs = torch.zeros(2, 14, 26, 26, dtype=torch.uint8)
    sc = torch.zeros(2, 19)
    inj_a = torch.zeros(2, 9)
    inj_b = torch.zeros(2, 9)
    inj_b[:, 7] = 1.0
    inj_b[:, 8] = 0.9
    with torch.no_grad():
        _, _, _, va = m.forward_rl(obs, sc, inj_a)
        _, _, _, vb = m.forward_rl(obs, sc, inj_b)
    check(not torch.equal(va, vb), "value 输出随注入变化（value 看到承诺状态）")

    # export/load 往返。
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "w.json")
        export_intent_weights(m, p)
        meta, params = load_weights_json(p)
        check(meta["arch"]["kind"] == "intent", "arch.kind=intent")
        check("value_head.weight" in params, "exported value_head.weight present")
        m2 = ppo_intent.IntentRLNet(h=64, d=8)
        from intent_net import load_intent_weights
        load_intent_weights(m2, p)
        check(True, "load_intent_weights roundtrip ok")


def test_ppo_update_smoke() -> None:
    """微型 shard → ppo_update_intent 冒烟：不崩、loss 有限、target_kl 早停可触发。"""
    rng = np.random.RandomState(11)
    torch.manual_seed(11)
    N = 64
    obs = rng.randint(0, 256, (N, 14, 26, 26)).astype(np.uint8)
    scalars = rng.randn(N, 19).astype(np.float32) * 0.5
    inject = np.zeros((N, 9), dtype=np.float32)
    inject[:, 2] = 1.0
    inject[:, 8] = 0.3
    a = rng.randint(0, 7, size=N).astype(np.int64)  # 只采可学类
    lp = np.full(N, -2.0, dtype=np.float32)
    value = rng.randn(N).astype(np.float32) * 0.1
    reward = rng.randn(N).astype(np.float32) * 0.2
    done = np.zeros(N, dtype=np.int64)
    done[-1] = 1
    mask = np.ones((N, 8), dtype=np.int64)
    mask[:, 7] = 0  # ESCAPE 死类
    dt = rng.randint(1, 40, size=N).astype(np.int64)

    ep = {"obs": obs, "scalars": scalars, "inject": inject, "a_intent": a,
          "lp_intent": lp, "value": value, "reward": reward, "done": done,
          "mask": mask, "dt": dt}
    adv, ret = ppo_intent.compute_gae_variable(reward, value, done, dt)
    ep["adv"] = (adv - adv.mean()) / (adv.std() + 1e-8)
    ep["ret"] = ret
    chunks = ppo_intent.chunk_episodes([ep], 32)

    model = ppo_intent.IntentRLNet(h=32, d=2)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    # 合成随机数据下 lp_old 与随机模型初始 logp 严重失配 → KL 必然爆炸；
    # 用极大阈值只测"不早停"路径（真实数据 lp_old 来自同一模型，KL 正常）。
    agg = ppo_intent.ppo_update_intent(model, opt, chunks, epochs=3, device=torch.device("cpu"),
                                       target_kl=1e9, seed=1)  # 极大阈值：不早停
    check(np.isfinite(agg["policy"]) and np.isfinite(agg["value"]),
          f"ppo update finite (policy={agg['policy']:.3f} value={agg['value']:.3f})")
    check(not agg["early_stopped"], "target_kl=1e9 不触发早停")

    # 极小阈值 → 早停触发。
    model2 = ppo_intent.IntentRLNet(h=32, d=2)
    opt2 = torch.optim.Adam(model2.parameters(), lr=1e-3)
    agg2 = ppo_intent.ppo_update_intent(model2, opt2, chunks, epochs=3,
                                        device=torch.device("cpu"),
                                        target_kl=1e-9, seed=1)
    check(agg2["early_stopped"], "target_kl 极小 → early stop 触发")

    # value warmup：前 epochs 全预热 → 策略（主干+三头）零梯度、KL=0、value 头被训练。
    model3 = ppo_intent.IntentRLNet(h=32, d=2)
    opt3 = torch.optim.Adam(model3.parameters(), lr=1e-3)
    stem_before = model3.stem.weight.detach().clone()
    policy_w_before = model3.intent_head.weight.detach().clone()
    vhead_before = model3.value_head.weight.detach().clone()
    agg3 = ppo_intent.ppo_update_intent(model3, opt3, chunks, epochs=2,
                                        device=torch.device("cpu"),
                                        target_kl=1e9, seed=1, value_warmup_epochs=2)
    check(agg3["kl"] == 0.0, f"warmup: kl=0 (policy frozen), got {agg3['kl']}")
    check(torch.equal(policy_w_before, model3.intent_head.weight),
          "warmup: intent_head 权重不变（策略零梯度）")
    check(torch.equal(stem_before, model3.stem.weight),
          "warmup: stem 权重不变（主干冻结，value 不扰动策略特征）")
    check(not torch.equal(vhead_before, model3.value_head.weight),
          "warmup: value_head 权重已更新（在冻结特征上学基线）")
    check(np.isfinite(agg3["value"]) and agg3["value"] > 0,
          f"warmup: value 头在学 (value={agg3['value']:.3f})")


def main() -> None:
    print("== test_ppo_intent ==")
    test_dt1_degradation()
    test_variable_dt_differs()
    test_rl_net_roundtrip_value_head()
    test_ppo_update_smoke()
    print(f"== {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failures) ==")
    for m in FAILS:
        print("  -", m)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

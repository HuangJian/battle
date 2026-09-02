"""test_ppo_common.py — ppo_common.py 共享 PPO 基础设施常驻回归测试。

覆盖（无真实训练、不碰节点）：
  1) compute_gae(dt=None) 定长路径 == 手算 GAE；dt=全 1 变步长路径与定长逐字节一致
     （P1-7k3 的 Δt≡1 退化保证，先例 test_ppo_intent.py::test_dt1_degradation）；
  2) dt≠1 时变步长确实产生差异（不恒等于定长）；
  3) masked_logsoftmax：无效位压 -inf、有效位 = log_softmax；
  4) cat_logprob / cat_entropy 基本性质；
  5) chunk_episodes：按 mb 切块、末块 ragged、键保持；
  6) checkpoint RNG 往返：_pack/_unpack 后 np.random 状态精确重建；
  7) discover_shards / load_shard_fields：marker 过滤 + 零拷贝 astype 字段表。

运行（经统一启动器进入 venv）：
  python test_ppo_common.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import numpy.typing as npt
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ppo.common as ppo_common

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"ok: {msg}")


def test_gae_dt1_degradation() -> None:
    """变步长 GAE（dt 全 1）与定长 GAE 逐字节一致（γ 同源）。"""
    rng = np.random.default_rng(123)
    N = 17
    rewards = rng.random(N).astype(np.float32)
    values = rng.random(N).astype(np.float32)
    dones = rng.integers(0, 2, N).astype(np.int64)
    gamma, lam = 0.995, 0.95
    dt = np.ones(N, dtype=np.int64)

    adv_fixed, ret_fixed = ppo_common.compute_gae(rewards, values, dones, gamma, lam)
    adv_var, ret_var = ppo_common.compute_gae(rewards, values, dones, gamma, lam, dt)
    check(np.array_equal(adv_fixed, adv_var), "GAE dt=1 → adv 与定长逐字节一致")
    check(np.array_equal(ret_fixed, ret_var), "GAE dt=1 → ret 与定长逐字节一致")


def test_gae_hand_computed() -> None:
    """定长 GAE 手算验证（N=3，已知数值）。"""
    rewards = np.array([1.0, 0.0, 2.0], dtype=np.float32)
    values = np.array([0.5, 0.6, 0.4], dtype=np.float32)
    dones = np.array([0, 0, 1], dtype=np.int64)
    gamma, lam = 0.9, 0.8
    adv, ret = ppo_common.compute_gae(rewards, values, dones, gamma, lam)
    # 手算：
    #   t=2 (terminal): delta=2.0+0-0.4=1.6 → adv2=1.6（last=1.6）
    #   t=1: delta=0.0+0.9*0.4-0.6=-0.24 → adv1=-0.24+0.9*0.8*1*1.6=0.912
    #   t=0: delta=1.0+0.9*0.6-0.5=1.04 → adv0=1.04+0.9*0.8*1*0.912=1.69664
    expected = np.array([1.69664, 0.912, 1.6], dtype=np.float32)
    check(np.allclose(adv, expected, atol=1e-5), f"定长 GAE 手算匹配 (got {adv})")
    check(np.allclose(ret, adv + values, atol=1e-5), "ret = adv + values")


def test_gae_variable_differs() -> None:
    """dt≠1 时变步长结果与定长不同（变步长确实生效）。"""
    rng = np.random.default_rng(7)
    N = 10
    rewards = rng.random(N).astype(np.float32)
    values = rng.random(N).astype(np.float32)
    dones = np.zeros(N, dtype=np.int64)
    gamma, lam = 0.995, 0.95
    dt = np.full(N, 3, dtype=np.int64)  # 每个意图步 = 3 tick
    adv_var, _ = ppo_common.compute_gae(rewards, values, dones, gamma, lam, dt)
    adv_fixed, _ = ppo_common.compute_gae(rewards, values, dones, gamma, lam)
    check(not np.allclose(adv_var, adv_fixed, atol=1e-6), "dt=3 的变步长 adv 与定长 adv 确有差异")


def test_masked_logsoftmax() -> None:
    logits = torch.tensor([[1.0, 2.0, 3.0], [0.5, -1.0, 4.0]])
    mask = torch.tensor([[1, 1, 0], [0, 1, 1]])
    out = ppo_common.masked_logsoftmax(logits, mask)
    # 无效位被压到极负（原实现用 -1e9 近似 -inf，与 ppo.py 历史行为一致）
    check(bool((out[0, 2] < -1e6).item()), "无效类 logp ≈ -inf（极负）")
    # 有效位 = 仅在有效子集上的 log_softmax
    exp_0 = torch.log_softmax(logits[0, :2], dim=-1)
    check(torch.allclose(out[0, :2], exp_0, atol=1e-6), "有效位 logp = log_softmax(有效子集)")
    # 概率和（exp）在有效位上 = 1
    probs = out.exp()
    check(
        bool(torch.isclose(probs[0, :2].sum(), torch.tensor(1.0), atol=1e-5).item()),
        "有效位概率和为 1",
    )


def test_cat_logprob_entropy() -> None:
    logp = torch.tensor([[0.1, 0.5, 0.4], [0.2, 0.3, 0.5]])
    a = torch.tensor([1, 2])
    got = ppo_common.cat_logprob(a, logp)
    check(torch.allclose(got, torch.tensor([0.5, 0.5])), "cat_logprob gather 正确")
    ent = ppo_common.cat_entropy(logp)
    manual = -(logp.exp() * logp).sum(dim=-1).mean()
    check(bool(torch.isclose(ent, manual).item()), "cat_entropy 与手算一致")


def test_chunk_episodes() -> None:
    # 显式标注：np.zeros 不同 dtype 的 ndarray join 会退化成 object，导致 ["obs"] 不可索引。
    eps: list[dict[str, np.ndarray]] = [
        {"obs": np.zeros((10, 3), dtype=np.uint8), "adv": np.zeros(10)},
        {"obs": np.zeros((5, 3), dtype=np.uint8), "adv": np.zeros(5)},
    ]
    chunks = ppo_common.chunk_episodes(eps, 4)
    sizes = [c["obs"].shape[0] for c in chunks]
    check(sizes == [4, 4, 2, 4, 1], f"chunk 尺寸 [4,4,2,4,1] (got {sizes})")
    check(all(set(c.keys()) == {"obs", "adv"} for c in chunks), "chunk 保留全部键")
    # 跨 episode 不混：每个 chunk 内部数据来自单一 episode
    check(
        chunks[0]["obs"][0, 0] == eps[0]["obs"][0, 0]
        and chunks[3]["obs"][0, 0] == eps[1]["obs"][0, 0],
        "chunk 不跨 episode 混数据",
    )


def test_np_state_roundtrip() -> None:
    np.random.seed(42)
    _ = np.random.permutation(100)  # 消耗一些状态
    packed = ppo_common._pack_np_state()
    expected = np.random.permutation(50).copy()
    ppo_common._unpack_np_state(packed)
    got = np.random.permutation(50).copy()
    check(np.array_equal(got, expected), "numpy RNG 状态 pack/unpack 精确重建")


def test_ppo_save_load(tmp_path: Path) -> None:
    import torch.nn as nn

    model = nn.Linear(4, 2)
    opt = torch.optim.Adam(model.parameters(), lr=0.1)
    td = tempfile.mkdtemp(dir=str(tmp_path))
    ppo_common._ppo_save(td, model, opt, 3)
    # 文件齐备
    for f in ("model.pt", "opt.pt", "state.json"):
        check(os.path.exists(os.path.join(td, f)), f"checkpoint 文件 {f} 存在")
    # 加载返回 epochs_done
    m2 = nn.Linear(4, 2)
    o2 = torch.optim.Adam(m2.parameters(), lr=0.1)
    done = ppo_common._ppo_load(td, m2, o2)
    check(done == 3, f"_ppo_load 返回 epochs_done=3 (got {done})")
    check(
        np.array_equal(m2.weight.detach().numpy(), model.weight.detach().numpy()),
        "checkpoint 加载后权重一致",
    )
    # 无 checkpoint 路径
    m3 = nn.Linear(4, 2)
    o3 = torch.optim.Adam(m3.parameters(), lr=0.1)
    check(ppo_common._ppo_load(None, m3, o3) == 0, "ckpt_path=None → 0")


def test_discover_and_load_shard_fields(tmp_path: Path) -> None:
    td = tempfile.mkdtemp(dir=str(tmp_path))
    root = Path(td)
    shard = root / "s1"
    shard.mkdir()
    np.save(shard / "obs.npy", np.zeros((4, 14, 26, 26), dtype=np.uint8))
    np.save(shard / "reward.npy", np.zeros(4, dtype=np.float32))
    np.save(shard / "dt.npy", np.ones(4, dtype=np.int64))
    # 缺 marker 的目录被过滤
    partial = root / "s2"
    partial.mkdir()
    np.save(partial / "obs.npy", np.zeros(1))
    found = ppo_common.discover_shards(str(root), ("reward.npy", "obs.npy"))
    check(found == [str(shard)], f"discover 只含完整 shard (got {found})")
    spec: dict[str, tuple[str, npt.DTypeLike]] = {
        "obs": ("obs.npy", np.uint8),
        "reward": ("reward.npy", np.float32),
        "dt": ("dt.npy", np.int64),
    }
    d = ppo_common.load_shard_fields(str(shard), spec)
    check(set(d.keys()) == {"obs", "reward", "dt"}, "load_shard_fields 字段齐备")
    check(d["obs"].dtype == np.uint8 and d["dt"].dtype == np.int64, "字段 dtype 按 spec 转换")


def main() -> None:
    # 手动运行入口：临时目录放项目内 tmp/（与 pytest basetemp 同区，避免碰系统 %TEMP% 触发沙箱权限）
    _td = tempfile.mkdtemp(dir=str(Path(__file__).resolve().parent.parent / 'tmp' / 'manual-tests'))
    test_gae_dt1_degradation()
    test_gae_hand_computed()
    test_gae_variable_differs()
    test_masked_logsoftmax()
    test_cat_logprob_entropy()
    test_chunk_episodes()
    test_np_state_roundtrip()
    test_ppo_save_load(Path(_td))
    test_discover_and_load_shard_fields(Path(_td))
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()

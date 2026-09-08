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
    test_chunk_episodes_shuffle_preserves_data()
    test_np_state_roundtrip()
    test_backend_params_match_config()
    test_ppo_save_load(Path(_td))
    test_discover_and_load_shard_fields(Path(_td))
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()


def test_chunk_episodes_shuffle_preserves_data() -> None:
    """P1-6：全局 transition 重排只改变顺序，不丢/不增数据；shuffle=False 保留旧行为。"""
    import numpy as np

    rng = np.random.RandomState(7)
    eps: list[dict] = [
        {"obs": rng.randint(0, 255, (12, 14, 26, 26)).astype(np.uint8),
         "adv": rng.randn(12).astype(np.float32),
         "ret": rng.randn(12).astype(np.float32)},
        {"obs": rng.randint(0, 255, (20, 14, 26, 26)).astype(np.uint8),
         "adv": rng.randn(20).astype(np.float32),
         "ret": rng.randn(20).astype(np.float32)},
    ]
    total = 32
    # shuffle=False：旧行为（逐字节等价）——首 chunk = 第一个 episode 的前 mb 行
    old_chunks = ppo_common.chunk_episodes(eps, mb=8, shuffle=False)
    np.testing.assert_array_equal(old_chunks[0]["obs"], eps[0]["obs"][:8])
    # shuffle=True：行数守恒 + 元素集合不变（拼回后与 flatten 一致）
    sh_chunks = ppo_common.chunk_episodes(eps, mb=8, shuffle=True)
    assert sum(c["obs"].shape[0] for c in sh_chunks) == total
    flat_adv = np.concatenate([e["adv"] for e in eps])
    got_adv = np.concatenate([c["adv"] for c in sh_chunks])
    assert sorted(got_adv.tolist()) == sorted(flat_adv.tolist()), "重排不丢/不增数据"
    # 重排确实发生（概率性断言：32 个连续索引全保持原序的概率 ≈ 0）
    first_flat = np.concatenate([e["adv"] for e in eps])
    assert not np.array_equal(got_adv, first_flat), "应发生重排"


def test_backend_params_match_config() -> None:
    """P1-1 守护：三后端本地超参与 ppo/config.py BACKEND_PARAMS 一致（调参必须同步）。"""
    from ppo.config import assert_backend_constants

    assert_backend_constants()


def _ret_norm_shards(tmp_path: Path) -> dict[str, dict[str, npt.NDArray]]:
    """两个 marker 目录 + 按名取数的 synthetic payload（ret 尺度不一，模拟
    R5 现场：value/return 量级差）。"""
    payloads: dict[str, dict[str, npt.NDArray]] = {}
    specs = [("shard_a", 10, 5.0, 2.0), ("shard_b", 6, -3.0, 0.5)]
    for name, n, base, step in specs:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "metrics.npy").write_bytes(b"0")
        (d / "obs.npy").write_bytes(b"0")
        rng = np.random.RandomState(11)
        payloads[name] = {
            "obs": rng.randint(0, 255, (n, 14, 26, 26)).astype(np.uint8),
            "scalars": rng.randn(n, 24).astype(np.float32),
            "a_move": rng.randint(0, 5, n).astype(np.int64),
            "a_fire": rng.randint(0, 2, n).astype(np.int64),
            "lp_move": rng.randn(n).astype(np.float32),
            "lp_fire": rng.randn(n).astype(np.float32),
            "value": rng.randn(n).astype(np.float32),
            "adv": (rng.randn(n) * 3 + 1).astype(np.float32),
            "ret": (np.arange(n, dtype=np.float32) * step + base),
            "mask": np.ones(n, dtype=np.int64),
            "reward": np.zeros(n, dtype=np.float32),
            "done": np.zeros(n, dtype=np.int64),
        }
    return payloads


def _ret_norm_call(
    tmp_path: Path, payloads: dict[str, dict[str, npt.NDArray]], normalize_ret: bool
) -> list[dict]:
    def loader(dirpath: str) -> dict[str, npt.NDArray]:
        return {k: v.copy() for k, v in payloads[Path(dirpath).name].items()}

    return ppo_common.load_episodes_common(
        str(tmp_path),
        label="ppo",
        shard_kind="RL",
        need_files=("metrics.npy", "obs.npy"),
        shard_loader=loader,
        gae=lambda d: (d["adv"], d["ret"]),
        gae_name="GAE",
        normalize_ret=normalize_ret,
    )


def test_load_episodes_common_ret_untouched_by_default(tmp_path: Path) -> None:
    """默认 normalize_ret=False：ret 逐字节不变（历史行为回归锚）；
    reward/done 字段照旧剥离，adv 仍全局归一。"""
    payloads = _ret_norm_shards(tmp_path)
    eps = _ret_norm_call(tmp_path, payloads, False)
    assert len(eps) == 2
    for ep in eps:
        assert "reward" not in ep and "done" not in ep
    got = np.concatenate([e["ret"] for e in eps])
    want = np.concatenate(
        [payloads["shard_a"]["ret"], payloads["shard_b"]["ret"]]
    )
    np.testing.assert_array_equal(got, want)


def test_load_episodes_common_ret_normalized_when_enabled(tmp_path: Path) -> None:
    """normalize_ret=True：ret 全局 mean 0 / std 1；obs 等载荷逐字节不动；
    adv 归一不受影响。"""
    payloads = _ret_norm_shards(tmp_path)
    eps = _ret_norm_call(tmp_path, payloads, True)
    got_ret = np.concatenate([e["ret"] for e in eps])
    assert abs(float(got_ret.mean())) < 1e-6
    assert abs(float(got_ret.std()) - 1.0) < 1e-6
    for name in ("shard_a", "shard_b"):
        ep = next(e for e in eps if e["obs"].shape[0] == payloads[name]["obs"].shape[0]
                  and np.array_equal(e["obs"], payloads[name]["obs"]))
        assert ep is not None
    got_adv = np.concatenate([e["adv"] for e in eps])
    assert abs(float(got_adv.mean())) < 1e-5


def _kick_chunks() -> list[dict]:
    """§363 kickstart 数值夹具：B=8 合成 minibatch（numpy 口径，与 tensored 一致）。"""
    rng = np.random.RandomState(3)
    n = 8
    return [
        {
            "obs": rng.randint(0, 255, (n, 14, 26, 26)).astype(np.uint8),
            "scalars": (rng.randn(n, 19)).astype(np.float32),
            "a_move": rng.randint(0, 5, n).astype(np.int64),
            "a_fire": rng.randint(0, 2, n).astype(np.int64),
            "lp_move": (rng.randn(n) * 0.2).astype(np.float32),
            "lp_fire": (rng.randn(n) * 0.2).astype(np.float32),
            "adv": (rng.randn(n)).astype(np.float32),
            "ret": (rng.randn(n) * 2 + 1).astype(np.float32),
            "mask": np.ones((n, 7), dtype=np.int64),
        }
    ]


def _kick_models():
    import torch

    from models.student import PPOStudent

    torch.manual_seed(0)
    m = PPOStudent()
    torch.manual_seed(0)
    m2 = PPOStudent()
    torch.manual_seed(1)
    ref = PPOStudent()
    ref.eval()
    for p in ref.parameters():
        p.requires_grad = False
    return torch, m, m2, ref


def _kick_state(m) -> list:
    return [p.detach().cpu().clone() for p in m.parameters()]


def test_ppo_update_kickstart_off_is_identity() -> None:
    """缺省路径恒等：ref=None（无论 kl 为何值）与 kl=0（无论 ref 有无）跑出逐字节
    相同的权重——旧行为回归锚。"""
    import ppo.engine as engine

    torch, m1, m2, ref = _kick_models()
    chunks = _kick_chunks()
    o1 = torch.optim.Adam(m1.parameters(), lr=1e-4)
    o2 = torch.optim.Adam(m2.parameters(), lr=1e-4)
    np.random.seed(7)
    a1 = engine.ppo_update(m1, o1, chunks, 1, torch.device("cpu"))
    np.random.seed(7)
    a2 = engine.ppo_update(
        m2, o2, chunks, 1, torch.device("cpu"), ref_model=ref, kickstart_kl=0.0
    )
    assert a1["kickstart"] == 0.0 and a2["kickstart"] == 0.0
    for p, q in zip(_kick_state(m1), _kick_state(m2), strict=True):
        assert torch.equal(p, q), "关闭路径权重必须逐字节一致"


def test_ppo_update_kickstart_bites_when_armed() -> None:
    """武装路径：ref 就绪＋kl>0 → kickstart 列 >0 且权重偏离关闭路径。"""
    import ppo.engine as engine

    torch, m1, m2, ref = _kick_models()
    chunks = _kick_chunks()
    o1 = torch.optim.Adam(m1.parameters(), lr=1e-4)
    o2 = torch.optim.Adam(m2.parameters(), lr=1e-4)
    np.random.seed(7)
    engine.ppo_update(m1, o1, chunks, 1, torch.device("cpu"))
    np.random.seed(7)
    agg = engine.ppo_update(
        m2, o2, chunks, 1, torch.device("cpu"), ref_model=ref, kickstart_kl=1.0
    )
    assert agg["kickstart"] > 0, "不同初始化的 ref 应有正 KL"
    assert any(
        not torch.equal(p, q) for p, q in zip(_kick_state(m1), _kick_state(m2), strict=True)
    ), "缰绳应改变更新轨迹"

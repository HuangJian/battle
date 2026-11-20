"""test_np_core.py — `ppo/np_core`（**免 torch** 的 numpy/stdlib 核心）常驻回归。

2026-09-26 从 `tests/worker/test_ppo_common.py` 分家：这些用例只吃 `ppo.np_core`（GAE、
minibatch 切分、numpy RNG 状态打包、shard 发现/字段表、episode 装载骨架的
ret 归一），却因同住一个 `import torch` 的测试文件而在无 torch 机上**收集失败**。
分家后本文件顶层零 torch：`np_core` 是 GAE / 装载的**家**，`ppo.common` 只是再导出。

覆盖：
  * compute_gae：定长手算 / dt=1 变步长退化 / dt≠1 确有差异；
  * chunk_episodes：mb 对齐（尾部丢弃）与 shuffle=False 对照路径、重排守恒、
    `shuffle=True` 缺 rng 必须响亮拒（§P1-7：不给全局 np.random 留后门）；
  * PPORng：`chunk` 流状态往返后逐位接上；
  * discover_shards / load_shard_fields：marker 过滤 + 零拷贝 astype 字段表；
  * load_episodes_common：normalize_ret off/on 的逐字节锚。

运行（经统一启动器进入 venv）：python test_np_core.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import numpy.typing as npt

from common.cli import run_main
from common.schema import OBS_CHANNELS

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import worker.ppo.np_core as np_core
from worker.ppo.rng import PPORng


def test_gae_dt1_degradation() -> None:
    """变步长 GAE（dt 全 1）与定长 GAE 逐字节一致（γ 同源）。"""
    rng = np.random.default_rng(123)
    N = 17
    rewards = rng.random(N).astype(np.float32)
    values = rng.random(N).astype(np.float32)
    dones = rng.integers(0, 2, N).astype(np.int64)
    gamma, lam = 0.995, 0.95
    dt = np.ones(N, dtype=np.int64)

    adv_fixed, ret_fixed = np_core.compute_gae(rewards, values, dones, gamma, lam)
    adv_var, ret_var = np_core.compute_gae(rewards, values, dones, gamma, lam, dt)
    assert np.array_equal(adv_fixed, adv_var), "GAE dt=1 → adv 与定长逐字节一致"
    assert np.array_equal(ret_fixed, ret_var), "GAE dt=1 → ret 与定长逐字节一致"

def test_gae_hand_computed() -> None:
    """定长 GAE 手算验证（N=3，已知数值）。"""
    rewards = np.array([1.0, 0.0, 2.0], dtype=np.float32)
    values = np.array([0.5, 0.6, 0.4], dtype=np.float32)
    dones = np.array([0, 0, 1], dtype=np.int64)
    gamma, lam = 0.9, 0.8
    adv, ret = np_core.compute_gae(rewards, values, dones, gamma, lam)
    # 手算：
    #   t=2 (terminal): delta=2.0+0-0.4=1.6 → adv2=1.6（last=1.6）
    #   t=1: delta=0.0+0.9*0.4-0.6=-0.24 → adv1=-0.24+0.9*0.8*1*1.6=0.912
    #   t=0: delta=1.0+0.9*0.6-0.5=1.04 → adv0=1.04+0.9*0.8*1*0.912=1.69664
    expected = np.array([1.69664, 0.912, 1.6], dtype=np.float32)
    assert np.allclose(adv, expected, atol=1e-5), f"定长 GAE 手算匹配 (got {adv})"
    assert np.allclose(ret, adv + values, atol=1e-5), "ret = adv + values"

def test_gae_variable_differs() -> None:
    """dt≠1 时变步长结果与定长不同（变步长确实生效）。"""
    rng = np.random.default_rng(7)
    N = 10
    rewards = rng.random(N).astype(np.float32)
    values = rng.random(N).astype(np.float32)
    dones = np.zeros(N, dtype=np.int64)
    gamma, lam = 0.995, 0.95
    dt = np.full(N, 3, dtype=np.int64)  # 每个意图步 = 3 tick
    adv_var, _ = np_core.compute_gae(rewards, values, dones, gamma, lam, dt)
    adv_fixed, _ = np_core.compute_gae(rewards, values, dones, gamma, lam)
    assert not np.allclose(adv_var, adv_fixed, atol=1e-6), "dt=3 的变步长 adv 与定长 adv 确有差异"

def test_chunk_episodes() -> None:
    """mb 对齐（2026-09-23，见 docs/nn.progress.md §140）。

    形状必须**逐块恰为 mb**：ragged 末块每 epoch 只用一次，隔几十个满块步就被 XLA
    程序缓存挤出 ⇒ 每 epoch 重编 ~12.5s（真机 it98：83.8s 里 50s 是它）。
    丢弃的是重排序列的尾部 ⇒ 均匀随机子集，无偏。
    两个不裁剪的分支有意保留：`shuffle=False`（对照路径逐字节不变）与单池
    （`len(episodes)<=1` 不重排 ⇒ 裁尾等于裁掉"局末"= 有偏，宁可不裁）。
    """
    # 显式标注：np.zeros 不同 dtype 的 ndarray join 会退化成 object，导致 ["obs"] 不可索引。
    eps: list[dict[str, np.ndarray]] = [
        {"obs": np.zeros((10, 3), dtype=np.uint8), "adv": np.zeros(10)},
        {"obs": np.zeros((5, 3), dtype=np.uint8), "adv": np.zeros(5)},
    ]
    rng = PPORng.from_seed(0)
    chunks = np_core.chunk_episodes(eps, 4, rng=rng.chunk)
    sizes = [c["obs"].shape[0] for c in chunks]
    assert sizes == [4, 4, 4], f"每块恰为 mb=4（15 步裁到 12）(got {sizes})"
    assert sum(sizes) == 12 == (15 // 4) * 4, "尾部 15%4=3 步丢弃"
    assert all(set(c.keys()) == {"obs", "adv"} for c in chunks), "chunk 保留全部键"
    # 对照路径：shuffle=False 逐字节保留旧做法（末块 ragged、不裁）。
    seq = np_core.chunk_episodes(eps, 4, shuffle=False)
    assert [c["obs"].shape[0] for c in seq] == [4, 4, 2, 4, 1], "shuffle=False 不改"
    assert (
        seq[0]["obs"][0, 0] == eps[0]["obs"][0, 0]
        and seq[3]["obs"][0, 0] == eps[1]["obs"][0, 0]
    ), "shuffle=False 不跨 episode 混数据"
    # 单池：不重排、不裁（裁尾有偏）。
    one = np_core.chunk_episodes([eps[0]], 4, rng=PPORng.from_seed(0).chunk)
    assert [c["obs"].shape[0] for c in one] == [4, 4, 2], "单池保持旧形状"
    # 池子 < mb：保留一块（裁成 0 块 = 静默不训练）。
    tiny = np_core.chunk_episodes([eps[0], eps[1]], 99, rng=PPORng.from_seed(0).chunk)
    assert [c["obs"].shape[0] for c in tiny] == [15], "池子 < mb 时不裁"

def test_chunk_episodes_shuffle_preserves_data() -> None:
    """P1-6：全局 transition 重排只改变顺序，不丢/不增数据；shuffle=False 保留旧行为。"""
    import numpy as np

    rng = np.random.RandomState(7)
    eps: list[dict] = [
        {"obs": rng.randint(0, 255, (12, OBS_CHANNELS, 26, 26)).astype(np.uint8),
         "adv": rng.randn(12).astype(np.float32),
         "ret": rng.randn(12).astype(np.float32)},
        {"obs": rng.randint(0, 255, (20, OBS_CHANNELS, 26, 26)).astype(np.uint8),
         "adv": rng.randn(20).astype(np.float32),
         "ret": rng.randn(20).astype(np.float32)},
    ]
    total = 32
    # shuffle=False：旧行为（逐字节等价）——首 chunk = 第一个 episode 的前 mb 行
    old_chunks = np_core.chunk_episodes(eps, mb=8, shuffle=False)
    np.testing.assert_array_equal(old_chunks[0]["obs"], eps[0]["obs"][:8])
    # shuffle=True：行数守恒 + 元素集合不变（拼回后与 flatten 一致）
    sh_chunks = np_core.chunk_episodes(eps, mb=8, shuffle=True, rng=PPORng.from_seed(7).chunk)
    assert sum(c["obs"].shape[0] for c in sh_chunks) == total
    flat_adv = np.concatenate([e["adv"] for e in eps])
    got_adv = np.concatenate([c["adv"] for c in sh_chunks])
    assert sorted(got_adv.tolist()) == sorted(flat_adv.tolist()), "重排不丢/不增数据"
    # 重排确实发生（概率性断言：32 个连续索引全保持原序的概率 ≈ 0）
    first_flat = np.concatenate([e["adv"] for e in eps])
    assert not np.array_equal(got_adv, first_flat), "应发生重排"

def test_chunk_episodes_requires_rng() -> None:
    """§P1-7：`shuffle=True` 不给 rng ⇒ ValueError（旧路径读全局 np.random，已拆）。

    为什么不是「None 就退回全局」：那等于留一条谁都能踩、谁也不会注意的后门 ——
    「同一 seed ⇒ 同一条轨迹」的前提就又没了。`shuffle=False` 不抽样，故不需要 rng。
    """
    # 两局起才走重排路径（单池不重排 ⇒ 本来就不需要 rng，见 `len(episodes) <= 1`）。
    eps: list[dict] = [
        {"obs": np.zeros((4, 3), dtype=np.uint8), "adv": np.zeros(4)},
        {"obs": np.zeros((4, 3), dtype=np.uint8), "adv": np.zeros(4)},
    ]
    try:
        np_core.chunk_episodes(eps, 2)
    except ValueError as e:
        assert "rng" in str(e), f"报错要指名 rng（got {e}）"
    else:
        raise AssertionError("chunk_episodes(shuffle=True) 缺 rng 必须 ValueError")
    # 对照：shuffle=False 照旧无需 rng
    seq = np_core.chunk_episodes(eps, 2, shuffle=False)
    assert [c["obs"].shape[0] for c in seq] == [2, 2, 2, 2], "shuffle=False 不需要 rng（两局各切两块）"


def test_ppo_rng_state_roundtrip() -> None:
    """ckpt 存的 `bit_generator.state` 灌回去 ⇒ 下一位抽样逐位相同（§P1-7）。"""
    a = PPORng.from_seed(123)
    _ = a.chunk.permutation(64)  # 先走一段，别停在原点
    blob = a.state()
    want = a.chunk.permutation(32).copy()
    b = PPORng.from_seed(123)  # 另一个同种子的实例，靠 restore 接上
    assert b.restore(blob), "restore 应接受自家 state"
    np.testing.assert_array_equal(b.chunk.permutation(32), want)
    assert a.epoch.permutation(16).tolist() == b.epoch.permutation(16).tolist(), "另两条流不受 chunk 消耗影响"
    # 旧 ckpt（全局 MT19937 时代）没有 rng_streams ⇒ False，且不抛
    assert PPORng.from_seed(0).restore(None) is False, "缺项 ⇒ False"
    assert PPORng.from_seed(0).restore({"chunk": {}}) is False, "坏项 ⇒ 整份拒收"

def test_discover_and_load_shard_fields(tmp_path: Path) -> None:
    td = tempfile.mkdtemp(dir=str(tmp_path))
    root = Path(td)
    shard = root / "s1"
    shard.mkdir()
    np.save(shard / "obs.npy", np.zeros((4, OBS_CHANNELS, 26, 26), dtype=np.uint8))
    np.save(shard / "reward.npy", np.zeros(4, dtype=np.float32))
    np.save(shard / "dt.npy", np.ones(4, dtype=np.int64))
    # 缺 marker 的目录被过滤
    partial = root / "s2"
    partial.mkdir()
    np.save(partial / "obs.npy", np.zeros(1))
    found = np_core.discover_shards(str(root), ("reward.npy", "obs.npy"))
    assert found == [str(shard)], f"discover 只含完整 shard (got {found})"
    spec: dict[str, tuple[str, npt.DTypeLike]] = {
        "obs": ("obs.npy", np.uint8),
        "reward": ("reward.npy", np.float32),
        "dt": ("dt.npy", np.int64),
    }
    d = np_core.load_shard_fields(str(shard), spec)
    assert set(d.keys()) == {"obs", "reward", "dt"}, "load_shard_fields 字段齐备"
    assert d["obs"].dtype == np.uint8 and d["dt"].dtype == np.int64, "字段 dtype 按 spec 转换"

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
            "obs": rng.randint(0, 255, (n, OBS_CHANNELS, 26, 26)).astype(np.uint8),
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

    return np_core.load_episodes_common(
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


# ---------------------------------------------------------------- GAE 手算 / 边界
# 2026-09-26（item 9）：自 `tests/worker/test_ppo_numerics.py` 分家——那三条只吃 np_core 的
# `compute_gae`（numpy 口径），却曾与 KL 三条（要真 torch 张量）同住一个文件而连坐。
def test_gae_matches_hand_computed() -> None:
    """GAE 定长路径：3 步例子逐值核对（γ=0.9, λ=0.8）。"""
    r = np.array([1.0, 0.0, 0.5], dtype=np.float32)
    v = np.array([0.2, 0.4, 0.3], dtype=np.float32)
    d = np.array([0, 0, 1], dtype=np.int64)
    adv, ret = np_core.compute_gae(r, v, d, 0.9, 0.8)
    # 手工推导（γ=0.9, λ=0.8）：
    # t=2: δ = 0.5 + 0.9*0 - 0.3 = 0.2;  A2 = 0.2
    # t=1: δ = 0.0 + 0.9*0.3 - 0.4 = -0.13; A1 = -0.13 + 0.9*0.8*1*0.2 = 0.014
    # t=0: δ = 1.0 + 0.9*0.4 - 0.2 = 1.16; A0 = 1.16 + 0.9*0.8*1*0.014 = 1.17008
    np.testing.assert_allclose(adv, [1.17008, 0.014, 0.2], atol=1e-5)
    np.testing.assert_allclose(ret, adv + v, atol=1e-5)

def test_gae_done_truncates_bootstrap() -> None:
    """done 截断：λ 递归在终止步之后必须清零（non_term 因子），但终止步**之前**
    的步仍正常延续。r=[0,0,0,0], v=[1,1,1,1], d=[0,1,0,1], γ=0.9, λ=0.95：
      t=3: δ = 0+0.9·0-1 = -1（无 next）        → A3 = -1
      t=2: δ = 0+0.9·1-1 = -0.1；A2 = -0.1 + 0.9·0.95·1·(-1) = -0.955（延续 A3）
      t=1: done → A1 = -0.1（non_term=0，递归清零）
      t=0: A0 = -0.1 + 0.9·0.95·1·(-0.1) = -0.1855（延续 A1）
    """
    r = np.zeros(4, dtype=np.float32)
    v = np.ones(4, dtype=np.float32)
    d = np.array([0, 1, 0, 1], dtype=np.int64)
    adv, _ = np_core.compute_gae(r, v, d, 0.9, 0.95)
    np.testing.assert_allclose(adv, [-0.1855, -0.1, -0.955, -1.0], atol=1e-5)

def test_gae_variable_dt_matches_fixed_when_dt1() -> None:
    """变步长 GAE（dt 数组）在 Δt≡1 时与定长路径逐字节一致。"""
    rng = np.random.default_rng(3)
    r = rng.standard_normal(20).astype(np.float32)
    v = rng.standard_normal(20).astype(np.float32)
    d = (rng.random(20) < 0.1).astype(np.int64)
    adv_fixed, ret_fixed = np_core.compute_gae(r, v, d, 0.995, 0.95)
    adv_var, ret_var = np_core.compute_gae(r, v, d, 0.995, 0.95, dt=np.ones(20, dtype=np.int64))
    np.testing.assert_array_equal(adv_var, adv_fixed)
    np.testing.assert_array_equal(ret_var, ret_fixed)


def _gae_reference(rewards, values, dones, gamma, lam, dt=None):
    """2026-11-16 向量化之前的循环实现，逐字保留 —— 独立重实现对账用（§8）。"""
    N = len(rewards)
    adv = np.zeros(N, dtype=np.float32)
    last = 0.0
    for t in reversed(range(N)):
        non_term = 1.0 - float(dones[t])
        gamma_step = float(gamma) if dt is None else float(gamma ** float(dt[t]))
        next_value = 0.0 if t + 1 >= N else float(values[t + 1])
        delta = float(rewards[t]) + gamma_step * next_value - float(values[t])
        last = delta + gamma_step * lam * non_term * last
        adv[t] = last
    return adv, adv + np.asarray(values, dtype=np.float32)

def test_gae_bit_exact_against_reference_loop() -> None:
    """P1-12 向量化必须是**逐字节**改写（不是 `allclose`）。

    `adv` 按 float64 累积再落 float32、`ret = adv + values` 也是 float32 加法，所以任何
    舍入顺序的改动都会动 golden 签名。六种长度 × 三种终止密度 × 三种 γ × 三种 dt 路径
    全覆盖，含 N=0 边界与 Python list 输入（Sequence 契约）+ 大动态范围。
    """
    rng = np.random.default_rng(20261116)
    cases = 0
    for n in (0, 1, 2, 5, 33, 200):
        r = rng.standard_normal(n).astype(np.float32)
        v = rng.standard_normal(n).astype(np.float32)
        for done_rate in (0.0, 0.05, 0.5):
            d = (rng.random(n) < done_rate).astype(np.int64)
            for gamma in (0.99, 0.995, 1.0):
                for dt in (None, rng.integers(0, 9, n).astype(np.int64),
                           rng.integers(1, 3, n).astype(np.float64)):
                    got = np_core.compute_gae(r, v, d, gamma, 0.95, dt)
                    want = _gae_reference(r, v, d, gamma, 0.95, dt)
                    for a, b in zip(got, want, strict=True):
                        assert a.dtype == np.float32 and b.dtype == np.float32
                        np.testing.assert_array_equal(a.view(np.int32), b.view(np.int32))
                    cases += 1
    big_r = [1e30, -1e30, 0.5, 0.0, 1.0, 2.0, 3.0]
    big_v = [1e30, 1e20, -3.0, 0.0, 0.0, 7.0, 8.0]
    big_d = [0, 0, 1, 0, 0, 0, 1]
    for dt in (None, np.full(7, 3, dtype=np.int64)):
        got = np_core.compute_gae(big_r, big_v, big_d, 1.0, 0.95, dt)
        want = _gae_reference(big_r, big_v, big_d, 1.0, 0.95, dt)
        for a, b in zip(got, want, strict=True):
            np.testing.assert_array_equal(a.view(np.int32), b.view(np.int32))
        cases += 1
    print(f"ok: GAE 向量化版与循环参照逐字节一致（{cases} 组）")


def main() -> None:
    _td = tempfile.mkdtemp(dir=str(Path(__file__).resolve().parents[2] / "tmp" / "manual-tests"))
    test_gae_dt1_degradation()
    test_gae_hand_computed()
    test_gae_variable_differs()
    test_gae_bit_exact_against_reference_loop()
    test_chunk_episodes()
    test_chunk_episodes_shuffle_preserves_data()
    test_chunk_episodes_requires_rng()
    test_ppo_rng_state_roundtrip()
    test_discover_and_load_shard_fields(Path(_td))
    print("\nAll tests passed.")


if __name__ == "__main__":
    run_main(main)

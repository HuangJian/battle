"""`sync_scalars` 批量同步回归（2026-09-10，三后端逐项 .item() -> 1 次同步）。

背景：per-tick 每梯度步原本 7-8 处 `.item()`，CPU 上近乎免费（实测 +4 ms/step），
但 CUDA 上每次都是全设备同步 —— 强制 drain 未执行的 kernel 队列，把 CPU/GPU 流水线
串行化（148 步/轮 x 8 = 1184 次/轮）。本测试守护三件事：

  1. **数值逐位不变**：`sync_scalars` 每个返回值 == 逐项 `float(t.item())`，
     含 float64 / 混合 dtype（栈会被提升到公共 dtype，不得截断）；
  2. **真的只有一次同步**：`torch.Tensor.tolist` 调用次数 == 1（这是本次改动的全部意义）；
  3. **三后端 stats 键集不变**：per-tick 8 键、intent 6 键、goal 7 键，且 intent/goal
     的 warmup 短路仍是精确 0.0（原先短路成 Python float，现改为 0 维零张量）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.student import PPOStudent
from ppo.common import sync_scalars
from ppo.engine import ppo_update

ENGINE_KEYS = {
    "policy",
    "value",
    "entropy",
    "kl",
    "kickstart",
    "mean_ret",
    "mean_adv",
    "gnorm",
}


# ---------------------------------------------------------------- 1) 数值等价
def test_values_bit_identical_to_elementwise_item() -> None:
    """每个返回值必须与逐项 float(t.item()) 完全相同（含混合 dtype）。"""
    vals = {
        "f32": torch.tensor(0.123456789, dtype=torch.float32),
        "f64": torch.tensor(0.123456789, dtype=torch.float64),
        "neg_small": torch.tensor(-1.2e-7, dtype=torch.float32),
        "zero": torch.zeros(()),
        "big": torch.tensor(1.7e5, dtype=torch.float32),
    }
    got = sync_scalars(vals)
    assert set(got) == set(vals)
    for k, t in vals.items():
        assert got[k] == float(t.item()), f"{k}: {got[k]!r} != {float(t.item())!r}"
        assert isinstance(got[k], float)


def test_mixed_dtype_not_truncated() -> None:
    """float32 + float64 混合时栈提升到 float64，float32 元素不得被截断。"""
    f32 = torch.tensor(1.0 / 3.0, dtype=torch.float32)
    f64 = torch.tensor(1.0 / 3.0, dtype=torch.float64)
    got = sync_scalars({"a": f32, "b": f64})
    assert got["a"] == float(f32.item())
    assert got["b"] == float(f64.item())
    assert got["a"] != got["b"], "float32 元素被提升后不应等于原始的 float64 值"


def test_empty_input_returns_empty() -> None:
    assert sync_scalars({}) == {}


# ---------------------------------------------------------------- 2) 只有一次同步
def test_single_sync_for_eight_scalars(monkeypatch) -> None:
    """8 个标量只触发 1 次 host 同步（这正是本次优化的全部要点）。"""
    calls = {"n": 0}
    real = torch.Tensor.tolist

    def counting(self, *a, **kw):
        calls["n"] += 1
        return real(self, *a, **kw)

    monkeypatch.setattr(torch.Tensor, "tolist", counting)
    out = sync_scalars({f"k{i}": torch.randn(()) for i in range(8)})
    assert len(out) == 8
    assert calls["n"] == 1, f"同步次数 {calls['n']}（应为 1）"


def test_single_element_tensor_is_reshaped() -> None:
    """1 元素张量（如 loss.reshape(1)）也能被规整为标量。"""
    got = sync_scalars({"x": torch.tensor([2.5])})
    assert got["x"] == 2.5


# ---------------------------------------------------------------- 3) 三后端键集
def _chunks(n_chunks: int = 3, b: int = 8, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_chunks):
        out.append(
            {
                "obs": rng.integers(0, 256, (b, 14, 26, 26), dtype=np.uint8),
                "scalars": rng.standard_normal((b, 19)).astype(np.float32),
                "a_move": rng.integers(0, 5, (b,), dtype=np.int64),
                "a_fire": rng.integers(0, 2, (b,), dtype=np.int64),
                "lp_move": rng.standard_normal(b).astype(np.float32),
                "lp_fire": rng.standard_normal(b).astype(np.float32),
                "adv": rng.standard_normal(b).astype(np.float32),
                "ret": rng.standard_normal(b).astype(np.float32),
                "mask": np.ones((b, 7), dtype=np.float32),
            }
        )
    return out


def test_engine_stats_keys_unchanged() -> None:
    """per-tick 后端聚合键集不得因批量化而变化。"""
    np.random.seed(3)
    torch.manual_seed(3)
    model = PPOStudent(h=8, d=1)
    opt = torch.optim.Adam(model.parameters(), lr=1.5e-4)
    agg = ppo_update(model, opt, _chunks(), 2, torch.device("cpu"))
    assert set(agg) == ENGINE_KEYS, f"键集变化：{set(agg) ^ ENGINE_KEYS}"
    for k, v in agg.items():
        assert np.isfinite(v), f"{k} 非有限值：{v!r}"


def _intent_fixture(n: int = 64, seed: int = 11):
    """复刻 tests/test_ppo_intent.py::test_ppo_update_smoke 的数据构造。"""
    import ppo.intent as ppo_intent

    rng = np.random.RandomState(seed)
    obs = rng.randint(0, 256, (n, 14, 26, 26)).astype(np.uint8)
    scalars = rng.randn(n, 19).astype(np.float32) * 0.5
    inject = np.zeros((n, 9), dtype=np.float32)
    inject[:, 2] = 1.0
    inject[:, 8] = 0.3
    a = rng.randint(0, 7, size=n).astype(np.int64)
    lp = np.full(n, -2.0, dtype=np.float32)
    value = rng.randn(n).astype(np.float32) * 0.1
    reward = rng.randn(n).astype(np.float32) * 0.2
    done = np.zeros(n, dtype=np.int64)
    done[-1] = 1
    mask = np.ones((n, 8), dtype=np.int64)
    mask[:, 7] = 0
    dt = rng.randint(1, 40, size=n).astype(np.int64)
    ep = {
        "obs": obs,
        "scalars": scalars,
        "inject": inject,
        "a_intent": a,
        "lp_intent": lp,
        "value": value,
        "reward": reward,
        "done": done,
        "mask": mask,
        "dt": dt,
    }
    adv, ret = ppo_intent.compute_gae_variable(reward, value, done, dt)
    ep["adv"] = (adv - adv.mean()) / (adv.std() + 1e-8)
    ep["ret"] = ret
    return ppo_intent, ppo_intent.chunk_episodes([ep], 32)


def test_intent_stats_keys_and_warmup_kl_exact_zero() -> None:
    """intent：键集不变；全 warmup 时 kl 必须是精确 0.0（原为 Python float 短路）。"""
    torch.manual_seed(11)
    ppo_intent, chunks = _intent_fixture()
    model = ppo_intent.IntentRLNet(h=32, d=2)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    agg = ppo_intent.ppo_update_intent(
        model,
        opt,
        chunks,
        epochs=2,
        device=torch.device("cpu"),
        target_kl=1e9,
        seed=1,
        value_warmup_epochs=2,  # 全 warmup -> kl 恒 0
    )
    for k in ("policy", "value", "entropy", "kl", "mean_ret", "gnorm"):
        assert k in agg, f"intent 缺键 {k}"
    assert agg["kl"] == 0.0, f"warmup 期 kl 应为精确 0.0，实得 {agg['kl']!r}"


def test_engine_aggregate_bit_identical_to_per_item_path(monkeypatch) -> None:
    """端到端 A/B：把 sync_scalars 换回「逐项 .item()」的老实现，聚合必须逐位相同。

    这是「批量化不改变数值」的最终证据 —— 不是靠读代码断言，而是同种子跑两遍比对。
    """
    import ppo.engine as eng

    chunks = _chunks(seed=5)

    np.random.seed(9)
    torch.manual_seed(9)
    model = PPOStudent(h=8, d=1)
    opt = torch.optim.Adam(model.parameters(), lr=1.5e-4)
    batched = ppo_update(model, opt, chunks, 2, torch.device("cpu"))

    def _per_item(vals: dict) -> dict:
        """老路径：每个标量各来一次 host 同步。"""
        return {k: float(v.detach().reshape(()).item()) for k, v in vals.items()}

    monkeypatch.setattr(eng, "sync_scalars", _per_item)
    np.random.seed(9)
    torch.manual_seed(9)
    model2 = PPOStudent(h=8, d=1)
    opt2 = torch.optim.Adam(model2.parameters(), lr=1.5e-4)
    per_item = ppo_update(model2, opt2, chunks, 2, torch.device("cpu"))

    assert set(batched) == set(per_item)
    for k in batched:
        assert batched[k] == per_item[k], f"{k}: {batched[k]!r} != {per_item[k]!r}"


def test_goal_stats_keys_include_bc() -> None:
    """goal：键集须含 bc（schema 兼容占位），且批量化后仍存在。"""
    import ppo.goal as ppo_goal

    torch.manual_seed(12)
    rng = np.random.RandomState(12)
    n = 64
    dim = ppo_goal.FINE_DIM
    obs = rng.randint(0, 256, (n, 14, 26, 26)).astype(np.uint8)
    scalars = rng.randn(n, 19).astype(np.float32) * 0.5
    inject = np.zeros((n, 9), dtype=np.float32)
    inject[:, 0] = 12 / 26
    inject[:, 1] = 9 / 26
    inject[:, 2] = 0.4
    a = rng.randint(0, dim // 2, size=n).astype(np.int64)  # 只采可学（未 mask）动作
    lp = np.full(n, -4.0, dtype=np.float32)
    value = rng.randn(n).astype(np.float32) * 0.1
    reward = rng.randn(n).astype(np.float32) * 0.2
    done = np.zeros(n, dtype=np.int64)
    done[-1] = 1
    mask = np.ones((n, dim), dtype=np.uint8)
    mask[:, dim // 2 :] = 0  # 一半动作不可达
    dt = rng.randint(60, 241, size=n).astype(np.int64)
    engage = rng.randint(0, 2, size=n).astype(np.int64)
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
    chunks = ppo_goal.chunk_episodes([ep], 32)

    model = ppo_goal.GoalRLNet(h=32, d=2)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    agg = ppo_goal.ppo_update_goal(
        model,
        opt,
        chunks,
        epochs=1,
        device=torch.device("cpu"),
        target_kl=1e9,
        seed=1,
    )
    for k in ("policy", "value", "entropy", "kl", "bc", "mean_ret", "gnorm"):
        assert k in agg, f"goal 缺键 {k}"
    assert agg["bc"] == 0.0

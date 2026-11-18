"""test_ppo_lazy_loader.py — P3 PPO lazy loader 的数值/顺序对账（plan/replay-corpus §5.2）。

覆盖：
  * eager vs lazy 同 seed：row_map / 全局行序 / GAE / adv・ret / minibatch /
    demo RNG / 最终权重逐字节一致；
  * `per_stage_quota` 截断在 GAE 之前（逐 shard 配额）；
  * `drop_last`（n % mb）与「单 shard 不裁尾」两条边界；
  * v6 可选列（a_hold/lp_hold）进字段集；
  * 冷/热 page cache 的 gather 吞吐读数（P3 要求分报；阈值只防灾难性回归）；
  * 491k 样本的 lazy 峰值预算 ≤ 2 GB（plan P3 gate 的公式锚）。

运行：`bash tools/githook/nn-py-safe.sh -m pytest tests/worker/test_ppo_lazy_loader.py`
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.reward_context import Scoped, reset
from biz.reward_library import METRICS_DIM
from worker.data.npyio import CorpusMemoryPlan
from worker.ppo import np_core
from worker.ppo.lazy_corpus import load_lazy_corpus


@pytest.fixture(autouse=True)
def _ctx_reset() -> Iterator[None]:
    reset()
    yield
    reset()


def _zero_reward(m: np.ndarray, _outcome: str, _score: float, _it: int) -> np.ndarray:
    """加载期奖励：全 0（本测试只对账装载顺序，奖励公式不是变量）。"""
    return np.zeros(int(m.shape[0]) - 1, dtype=np.float32)


def _write_shard(
    root: Path, name: str, n: int, *, stage: int = 0, v6: bool = False, seed: int = 1
) -> Path:
    """合成一个 per-tick RL shard（字段名/宽度与 export-rl-rollout.ts 同源）。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed * 7919 + n)
    np.save(d / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.random((n, SCALAR_DIM), dtype=np.float32))
    np.save(d / "extra.npy", rng.random((n, POLICY_EXTRA_DIM), dtype=np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 4 if v6 else 5, n, dtype=np.int64))
    np.save(d / "a_fire.npy", rng.integers(0, 3 if v6 else 2, n, dtype=np.int64))
    np.save(d / "lp_move.npy", (rng.random(n).astype(np.float32) - 2.0))
    np.save(d / "lp_fire.npy", (rng.random(n).astype(np.float32) - 2.0))
    if v6:
        np.save(d / "a_hold.npy", rng.integers(0, 2, n, dtype=np.int64))
        np.save(d / "lp_hold.npy", (rng.random(n).astype(np.float32) - 2.0))
    np.save(d / "value.npy", (rng.random(n).astype(np.float32) * 0.1))
    # 奖励由加载期公式算：只要 [N+1, METRICS_DIM] f8 与 reward_fn 在场。
    np.save(d / "metrics.npy", np.zeros((n + 1, METRICS_DIM), dtype=np.float64))
    np.save(d / "done.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 9 if v6 else 7), dtype=np.int64))
    (d / "manifest.json").write_text(
        json.dumps({"stage": stage, "nSamples": n, "outcome": "timeout", "score": 0.0}),
        encoding="utf-8",
    )
    return d


def _eager(root: Path, **kwargs) -> list[dict]:
    with Scoped(reward_fn=_zero_reward, it=1):
        return np_core.load_episodes(str(root), **kwargs)


def _lazy(root: Path, **kwargs):
    with Scoped(reward_fn=_zero_reward, it=1):
        return load_lazy_corpus(str(root), **kwargs)


def test_loader_default_is_lazy() -> None:
    """P3 收尾（2026-11-17）：生产缺省 = lazy，回滚靠**显式** eager。

    三处缺省必须一致（`worker/cli.py` 的 argparse ← rl-config / `ppo/engine.py` 的独立 CLI /
    课程的文档值）；否则「课程写 lazy、实际跑 eager」这类错账只有日志能看出来。
    """
    from worker.cli import build_argparser

    p = build_argparser("per-tick", {})
    assert p.parse_args([]).ppo_loader == "lazy"
    # rl-config 里显式写 eager 仍然赢（回滚档，不许被缺省盖掉）。
    p2 = build_argparser("per-tick", {"ppo_loader": "eager"})
    assert p2.parse_args([]).ppo_loader == "eager"

    from biz.course_spec import CourseConfig

    assert CourseConfig.model_fields["ppo_loader"].default == "lazy"


def _chunk_count(chunks) -> int:
    sizes = getattr(chunks, "sizes", None)
    return len(sizes) if sizes is not None else len(chunks)


def _chunk_at(chunks, i: int) -> Any:
    get = getattr(chunks, "get", None)
    return get(i) if callable(get) else chunks[i]


def _assert_same_chunks(a: list[dict], b) -> None:
    na, nb = _chunk_count(a), _chunk_count(b)
    assert na == nb, f"chunk 数不同：{na} vs {nb}"
    for i in range(na):
        ca, cb = _chunk_at(a, i), _chunk_at(b, i)
        assert set(ca) == set(cb), f"chunk {i} 键集不同：{set(ca) ^ set(cb)}"
        for k in ca:
            np.testing.assert_array_equal(
                ca[k], cb[k], err_msg=f"chunk {i} 字段 {k} 不一致（eager vs lazy）"
            )


def _parity(root: Path, mb: int, **load_kwargs):
    """同 seed 跑 eager/lazy 两条装载 + 切块，返回两边的 chunks（顺带校验 RNG 流）。"""
    np.random.seed(123)
    eps = _eager(root, **load_kwargs)
    chunks_e = np_core.chunk_episodes(eps, mb)
    after_e = np.random.permutation(19)

    np.random.seed(123)
    corpus = _lazy(root, mb=mb, **load_kwargs)
    chunks_l = np_core.chunk_episodes(corpus, mb)
    after_l = np.random.permutation(19)

    np.testing.assert_array_equal(after_e, after_l, err_msg="两条路径的 RNG 消费不同")
    _assert_same_chunks(chunks_e, chunks_l)
    return eps, corpus, chunks_e, chunks_l


def test_eager_lazy_row_map_and_chunks_identical(tmp_path: Path) -> None:
    _write_shard(tmp_path, "s1", 23, stage=0, seed=1)
    _write_shard(tmp_path, "s2", 17, stage=0, seed=2)
    _write_shard(tmp_path, "s3", 31, stage=1, seed=3)
    eps, corpus, _, chunks_l = _parity(tmp_path, mb=8)

    # row_map：global_row → (shard, local_row)，gather 结果必须等于 eager 的 concatenate。
    flat = np.concatenate([e["obs"] for e in eps], axis=0)
    assert corpus.samples == flat.shape[0]
    got = corpus.gather(np.arange(corpus.samples))["obs"]
    np.testing.assert_array_equal(got, flat, err_msg="row_map/gather 与 eager 行序不一致")
    # 显式 row_map 自洽：每行指向的 shard/局部行确实等于全局行。
    starts = np.concatenate([[0], np.cumsum(corpus._shard_sizes)[:-1]])
    assert np.array_equal(starts[corpus.row_shard] + corpus.row_local, np.arange(corpus.samples))
    assert chunks_l.loader == "lazy" and chunks_l.peak_mb > 0


def test_eager_lazy_per_stage_quota_trims_before_gae(tmp_path: Path) -> None:
    # 同一 stage 两片：配额 12 ⇒ 第一片全收(10)、第二片只留 2（截断在 GAE 之前）。
    _write_shard(tmp_path, "a", 10, stage=7, seed=1)
    _write_shard(tmp_path, "b", 9, stage=7, seed=2)
    _write_shard(tmp_path, "c", 8, stage=8, seed=3)
    _parity(tmp_path, mb=4, per_stage_quota=12)


def test_eager_lazy_drop_last_and_single_shard(tmp_path: Path) -> None:
    # 26 行 / mb=8 ⇒ 丢尾 2 行；eager 的 idx 顺序不动。
    _write_shard(tmp_path, "s1", 15, seed=1)
    _write_shard(tmp_path, "s2", 11, seed=2)
    _, _, chunks_e, _ = _parity(tmp_path, mb=8)
    assert [c["obs"].shape[0] for c in chunks_e] == [8, 8, 8], "26 行应裁成 3×8"

    # 单 shard：eager 走 `len(episodes)<=1` 分支（不重排、不裁尾），lazy 必须一致
    # 且**不消费 RNG**（同一 seed 下后续 permutation 相同）。
    one = tmp_path / "one"
    _write_shard(one, "s1", 10, seed=5)
    np.random.seed(77)
    eps1 = _eager(one)
    ch1 = np_core.chunk_episodes(eps1, 4)
    after1 = np.random.permutation(11)
    np.random.seed(77)
    corpus1 = _lazy(one, mb=4)
    ch2 = np_core.chunk_episodes(corpus1, 4)
    after2 = np.random.permutation(11)
    np.testing.assert_array_equal(after1, after2, "单 shard 分支不得消费 RNG")
    _assert_same_chunks(ch1, ch2)
    assert [_chunk_at(ch2, i)["obs"].shape[0] for i in range(_chunk_count(ch2))] == [4, 4, 2]


def test_eager_lazy_v6_optional_fields(tmp_path: Path) -> None:
    _write_shard(tmp_path, "v1", 13, v6=True, seed=1)
    _write_shard(tmp_path, "v2", 9, v6=True, seed=2)
    _, _, chunks_e, _ = _parity(tmp_path, mb=6)
    assert {"a_hold", "lp_hold"} <= set(chunks_e[0])
    assert chunks_e[0]["mask"].shape[1] == 9


def test_lazy_rejects_mixed_v6_legacy(tmp_path: Path) -> None:
    _write_shard(tmp_path, "v6", 6, v6=True, seed=1)
    _write_shard(tmp_path, "legacy", 6, v6=False, seed=2)
    with pytest.raises(ValueError, match="v6/legacy 语料混装"):
        _lazy(tmp_path, mb=4)


def _ppo_update_parity(tmp_path: Path) -> None:
    """同 seed 跑 eager chunks 与 lazy chunks 的完整 ppo_update，最终权重逐字节一致。"""
    import torch

    from worker.models.student import PPOStudent
    from worker.ppo.engine import ppo_update

    np.random.seed(5)
    eps = _eager(tmp_path)
    chunks_e = np_core.chunk_episodes(eps, 8)
    np.random.seed(5)
    corpus = _lazy(tmp_path, mb=8)
    chunks_l = np_core.chunk_episodes(corpus, 8)
    _assert_same_chunks(chunks_e, chunks_l)

    torch.manual_seed(0)
    m1 = PPOStudent()
    torch.manual_seed(0)
    m2 = PPOStudent()
    torch.manual_seed(1)
    ref = PPOStudent()
    ref.eval()
    for p in ref.parameters():
        p.requires_grad = False
    rng = np.random.default_rng(9)
    demo = {
        "obs": rng.integers(0, 256, (20, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
        "scalars": rng.random((20, SCALAR_DIM), dtype=np.float32),
        "actions": rng.integers(0, np.array([5, 2]), size=(20, 2), dtype=np.int64),
        "masks": np.ones((20, 7), dtype=np.float32),
    }
    o1 = torch.optim.Adam(m1.parameters(), lr=1e-4)
    o2 = torch.optim.Adam(m2.parameters(), lr=1e-4)
    kw: dict[str, Any] = dict(
        kl_coef=0.02,
        ref_model=ref,
        kickstart_kl=0.5,
        demo_bank=demo,
        demo_bc_coef=0.5,
        demo_per_mb=4,
        value_anchor=0.1,
    )
    np.random.seed(31)
    a1 = ppo_update(m1, o1, chunks_e, 2, torch.device("cpu"), **kw)
    np.random.seed(31)
    a2 = ppo_update(m2, o2, chunks_l, 2, torch.device("cpu"), lazy=True, **kw)
    for p, q in zip(m1.parameters(), m2.parameters(), strict=True):
        assert torch.equal(p, q), "lazy 与 eager 的最终权重必须逐字节一致"
    for k in a1:
        assert a1[k] == pytest.approx(a2[k], rel=0, abs=1e-12), f"统计量 {k} 不一致"


def test_ppo_update_eager_lazy_final_weights_identical(tmp_path: Path) -> None:
    _write_shard(tmp_path, "s1", 12, seed=1)
    _write_shard(tmp_path, "s2", 12, seed=2)
    _ppo_update_parity(tmp_path)


def test_ppo_update_lazy_transfers_only_per_batch(tmp_path: Path, monkeypatch) -> None:
    """小 VRAM gate：lazy 路径不得建全量 torch 副本，每步只搬当前 mb 行。

    判据两条：① `tensored_chunks` 被调用即失败（那就是全量副本）；
    ② `tensored_batch` 的每批 obs 形状恰为 `(mb, C, B, B)`，调用次数 = chunks × epochs。
    """
    import torch

    from worker.ppo import engine as eng

    _write_shard(tmp_path, "s1", 16, seed=1)
    corpus = _lazy(tmp_path, mb=8)
    chunks = np_core.chunk_episodes(corpus, 8)
    assert cast(Any, chunks).sizes == [8, 8]

    seen: list[tuple[int, ...]] = []
    real_batch = eng.tensored_batch

    def _spy(item, device, memory_format=None):
        seen.append(tuple(item["obs"].shape))
        return real_batch(item, device, memory_format)

    def _boom(*_a, **_k):
        raise AssertionError("lazy 路径不得调用 tensored_chunks（全量副本会顶爆小 VRAM）")

    monkeypatch.setattr(eng, "tensored_batch", _spy)
    monkeypatch.setattr(eng, "tensored_chunks", _boom)

    class Tiny(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = torch.nn.Linear(OBS_CHANNELS * BOARD * BOARD + SCALAR_DIM, 16)
            self.mv = torch.nn.Linear(16, 5)
            self.fr = torch.nn.Linear(16, 2)
            self.v = torch.nn.Linear(16, 1)

        def forward(self, obs, sc, extra=None):
            h = torch.relu(self.net(torch.cat([obs.flatten(1).float(), sc], dim=1)))
            return self.mv(h), self.fr(h), self.v(h)

    torch.manual_seed(0)
    model = Tiny()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    np.random.seed(3)
    eng.ppo_update(model, opt, chunks, 2, torch.device("cpu"), lazy=True)
    assert seen == [(8, OBS_CHANNELS, BOARD, BOARD)] * 4, (
        f"每批只应搬 mb=8 行（2 chunks × 2 epochs），实得 {seen}"
    )


def test_lazy_gather_throughput_readout(tmp_path: Path) -> None:
    """P3 的冷/热 page cache 读数（阈值只防灾难性回归，不做机器间比较）。"""
    for i in range(6):
        _write_shard(tmp_path, f"s{i}", 200, seed=i + 1)
    corpus = _lazy(tmp_path, mb=100)
    chunks = np_core.chunk_episodes(corpus, 100)
    n = _chunk_count(chunks)
    nbytes = sum(_chunk_at(chunks, i)["obs"].nbytes for i in range(n))

    t0 = time.perf_counter()
    for i in range(n):
        _ = _chunk_at(chunks, i)["obs"].sum()
    cold = time.perf_counter() - t0

    t0 = time.perf_counter()
    for i in range(n):
        _ = _chunk_at(chunks, i)["obs"].sum()
    hot = time.perf_counter() - t0

    cold_mb = nbytes / (1 << 20) / max(cold, 1e-9)
    hot_mb = nbytes / (1 << 20) / max(hot, 1e-9)
    print(f"lazy gather: cold={cold_mb:.0f} MB/s hot={hot_mb:.0f} MB/s（{nbytes >> 10} KiB）")
    assert hot_mb > 5.0, f"热缓存 gather 吞吐 {hot_mb:.1f} MB/s < 5 MB/s（疑似退回整份物化）"


def test_lazy_peak_budget_under_2gb_for_491k() -> None:
    """plan P3 gate：491k 样本、mb=1024 的 lazy 峰值 ≤ 2 GB（不含 page cache）。"""
    per_sample = OBS_CHANNELS * BOARD * BOARD
    plan = CorpusMemoryPlan(
        "synthetic-35x20",
        shards=700,
        samples=491520,
        obs_bytes=491520 * per_sample,
        other_bytes=210 << 20,  # scalars/extra/动作/lp/mask/metrics ≈ 200 MB
    )
    peak = plan.lazy_peak_bytes(batch_size=1024)
    assert peak <= (2 << 30), f"lazy 峰值 {peak / (1 << 30):.2f} GiB 超过 2 GiB gate"


# ───────────────────── Paddle 双生（2026-11-18）：lazy 不再是 torch 专属 ─────────────────────
# 本机 venv 没有 paddle ⇒ 这两条 skip；AI Studio / Paddle 真机跑时钉住「与 eager 逐位一致」。
# 判据与 torch 版同源：同 seed 的 eager/lazy 最终权重逐字节 + 统计量一致，且 lazy 不上全量副本。


def test_load_ppo_episodes_passes_manifest_loader(monkeypatch) -> None:
    """两条训练核共用的分派点：manifest 写什么就传什么，缺键保持协议缺省 eager。"""
    import worker.ppo.lazy_corpus as lc
    from remote.train_common import load_ppo_episodes

    seen: dict[str, Any] = {}
    sentinel = object()

    def _fake(root, **kwargs):
        seen.update({"root": root, **kwargs})
        return sentinel

    monkeypatch.setattr(lc, "load_ppo_corpus", _fake)
    manifest = {
        "ppo_loader": "lazy",
        "gamma": 0.99,
        "lam": 0.95,
        "adv_norm": "batch",
        "normalize_ret": True,
        "per_stage_quota": 12,
        "mb": 1024,
    }
    assert load_ppo_episodes("root", manifest, None) is sentinel
    assert seen["loader"] == "lazy"
    assert seen["mb"] == 1024 and seen["per_stage_quota"] == 12
    assert seen["root"] == "root" and seen["bundle"] is None
    manifest.pop("ppo_loader")
    load_ppo_episodes("root", manifest, None)
    assert seen["loader"] == "eager", "协议缺省必须仍是 eager（旧 manifest 逐字节旧路径）"


def test_paddle_ppo_update_eager_lazy_final_weights_identical(tmp_path: Path) -> None:
    paddle = pytest.importorskip("paddle")

    from worker.models.student_paddle import PPOStudent
    from worker.ppo import engine_paddle

    _write_shard(tmp_path, "s1", 12, seed=1)
    _write_shard(tmp_path, "s2", 12, seed=2)
    np.random.seed(5)
    chunks_e = np_core.chunk_episodes(_eager(tmp_path), 8)
    np.random.seed(5)
    chunks_l = np_core.chunk_episodes(_lazy(tmp_path, mb=8), 8)
    _assert_same_chunks(chunks_e, chunks_l)

    paddle.set_device("cpu")
    paddle.seed(0)
    m1 = PPOStudent(h=8, d=1, head_hidden=16)
    paddle.seed(0)
    m2 = PPOStudent(h=8, d=1, head_hidden=16)
    paddle.seed(1)
    ref = PPOStudent(h=8, d=1, head_hidden=16)
    ref.eval()
    for p in ref.parameters():
        p.stop_gradient = True
    rng = np.random.default_rng(9)
    demo = {
        "obs": rng.integers(0, 256, (20, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
        "scalars": rng.random((20, SCALAR_DIM), dtype=np.float32),
        "actions": rng.integers(0, np.array([5, 2]), size=(20, 2), dtype=np.int64),
        "masks": np.ones((20, 7), dtype=np.float32),
    }
    o1 = paddle.optimizer.Adam(learning_rate=1e-4, parameters=m1.parameters())
    o2 = paddle.optimizer.Adam(learning_rate=1e-4, parameters=m2.parameters())
    kw: dict[str, Any] = dict(
        kl_coef=0.02,
        ref_model=ref,
        kickstart_kl=0.5,
        demo_bank=demo,
        demo_bc_coef=0.5,
        demo_per_mb=4,
        value_anchor=0.1,
    )
    np.random.seed(31)
    a1 = engine_paddle.ppo_update(m1, o1, chunks_e, 2, **kw)
    np.random.seed(31)
    a2 = engine_paddle.ppo_update(m2, o2, chunks_l, 2, lazy=True, **kw)
    for name, p1 in m1.state_dict().items():
        np.testing.assert_array_equal(
            p1.numpy(), m2.state_dict()[name].numpy(), err_msg=f"lazy 与 eager 的 {name} 必须逐字节一致"
        )
    for k in a1:
        assert a1[k] == pytest.approx(a2[k], rel=0, abs=1e-12), f"统计量 {k} 不一致"


def test_paddle_lazy_transfers_only_per_batch(tmp_path: Path, monkeypatch) -> None:
    """Paddle lazy 也不得建全量副本：每步只搬当前 mb 行。"""
    paddle = pytest.importorskip("paddle")

    from worker.ppo import engine_paddle as eng

    _write_shard(tmp_path, "s1", 16, seed=1)
    chunks = np_core.chunk_episodes(_lazy(tmp_path, mb=8), 8)
    seen: list[tuple[int, ...]] = []
    real_batch = eng.tensored_batch

    def _spy(item):
        seen.append(tuple(item["obs"].shape))
        return real_batch(item)

    def _boom(*_a, **_k):
        raise AssertionError("paddle lazy 路径不得调用 tensored_chunks（全量副本会顶爆显存）")

    monkeypatch.setattr(eng, "tensored_batch", _spy)
    monkeypatch.setattr(eng, "tensored_chunks", _boom)

    class Tiny:
        """引擎只要 callable + `parameters()`/`train()`。

        不继承 `paddle.nn.Layer`：基类表达式不能引用函数内的局部名（mypy `name-defined`）。
        """

        def __init__(self) -> None:
            self.net = paddle.nn.Linear(OBS_CHANNELS * BOARD * BOARD + SCALAR_DIM, 16)
            self.mv = paddle.nn.Linear(16, 5)
            self.fr = paddle.nn.Linear(16, 2)
            self.v = paddle.nn.Linear(16, 1)

        def parameters(self):
            return [
                *self.net.parameters(),
                *self.mv.parameters(),
                *self.fr.parameters(),
                *self.v.parameters(),
            ]

        def train(self) -> None:
            return None

        def __call__(self, obs, sc, extra=None):
            flat = obs.reshape([obs.shape[0], -1]).astype("float32")
            h = paddle.nn.functional.relu(self.net(paddle.concat([flat, sc], axis=1)))
            return self.mv(h), self.fr(h), self.v(h)

    paddle.set_device("cpu")
    paddle.seed(0)
    model = Tiny()
    opt = paddle.optimizer.Adam(learning_rate=1e-3, parameters=model.parameters())
    np.random.seed(3)
    eng.ppo_update(model, opt, chunks, 2, lazy=True)
    assert seen == [(8, OBS_CHANNELS, BOARD, BOARD)] * 4, (
        f"每批只应搬 mb=8 行（2 chunks × 2 epochs），实得 {seen}"
    )

"""v6 三头 BC 数据链（2026-11-04 落地）的接线测试。

钉三件事（都不吃真语料、峰值内存 < 20 MB）：
  1. `npyio._load_dataset_v6` 从 `a_move/a_fire/a_hold + mask.npy(9)` 拼出与 legacy 同键的 dict；
  2. `NNDataset(action_v6=True)` 返回 **10 元组**，且 mask 按 4/3/2 切成三段；
  3. 三个头各自 CE 能算，且 **move 的 target 不会越过 4 类**（`mirror_x` 那条 legacy 5 类表
     曾经把 v6 的 4 类映射出 `Target 4`，这里钉住 `mirror_p=0` 的绕行）。

为什么不调 `train()`：它要完整 args + 落盘，且 44.7 万样本的语料会吃 11 GB 内存——
真正的端到端验证放在云机（`bc` 课程，32 G 内存）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from worker.data.dataset import NNDataset, make_loaders
from worker.data.npyio import load_dataset

N = 64  # 每 shard 样本数（够切 train/val）
CH, BOARD = 17, 26
SCALAR_DIM, EXTRA_DIM = 46, 57


def _write_shard(root: Path, name: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "obs.npy", rng.integers(0, 256, (N, CH, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((N, SCALAR_DIM)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((N, EXTRA_DIM)).astype(np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 4, N, dtype=np.uint8))  # v6：0..3，无 STOP
    np.save(d / "a_fire.npy", rng.integers(0, 3, N, dtype=np.uint8))  # v6：3 类
    np.save(d / "a_hold.npy", rng.integers(0, 2, N, dtype=np.uint8))  # v6：2 类
    np.save(d / "mask.npy", np.ones((N, 9), dtype=np.uint8))  # 9 位全合法
    (d / "manifest.json").write_text(
        json.dumps({"outcome": "stage_clear", "samples": N}), encoding="utf-8"
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    for i in range(4):
        _write_shard(tmp_path, f"rl_s2000_seed{i}", 100 + i)
    return tmp_path


def test_load_dataset_v6_keys_and_shapes(corpus: Path) -> None:
    data = load_dataset(str(corpus), action_v6=True)
    n = N * 4
    assert data["obs"].shape == (n, CH, BOARD, BOARD)
    assert data["hold"].shape == (n,)
    assert data["mask"].shape == (n, 9)
    assert data["actions"].shape == (n, 2)
    # 兼容层：actions 的第 0 列就是 a_move（0..3）
    assert int(data["actions"][:, 0].max()) <= 3
    assert int(data["actions"][:, 1].max()) <= 2
    assert data["conditions"].shape == (n,)


def test_dataset_returns_ten_tuple_with_split_masks(corpus: Path) -> None:
    data = load_dataset(str(corpus), action_v6=True)
    ds = NNDataset(data, augment=True, mirror_p=0.5, action_v6=True)
    item = ds[0]
    assert len(item) == 10, "v6 必须是 10 元组（多 hold + 它的 mask）"
    _obs, _sc, _ex, mv, fr, hd, mm, mf, mh, _ret = item
    assert 0 <= int(mv) <= 3, "v6 的 move 只有 4 类（无 STOP）"
    assert 0 <= int(fr) <= 2
    assert 0 <= int(hd) <= 1
    assert mm.shape == (4,) and mf.shape == (3,) and mh.shape == (2,)
    # 全局关键：augment 打开也不许把 move 映到 4（legacy 5 类表的老坑）
    for i in range(len(ds)):
        assert 0 <= int(ds[i][3]) <= 3


def test_make_loaders_v6_and_three_head_ce(corpus: Path) -> None:
    train_dl, val_dl, sizes = make_loaders(
        str(corpus), batch_size=32, val_split=0.25, action_v6=True
    )
    assert sizes["total"] == N * 4
    batch = next(iter(train_dl))
    assert len(batch) == 10
    obs, sc, ex, mv, fr, hd, mm, mf, mh, _ret = batch
    assert obs.dtype == torch.uint8

    from worker.models.student import PPOStudent

    model = PPOStudent(action_v6=True)
    out = model(obs, sc, ex)
    assert len(out) == 4, "v6 的 PPOStudent.forward 必须返回 4 元组"
    lm, lf, lh, vpred = out
    assert lm.shape[1] == 4 and lf.shape[1] == 3 and lh.shape[1] == 2
    assert vpred.shape[0] == obs.shape[0]
    # 三个头各自 CE 都要能算（不经 mask，直接对 target）
    ce = (
        torch.nn.functional.cross_entropy(lm, mv.long())
        + torch.nn.functional.cross_entropy(lf, fr.long())
        + torch.nn.functional.cross_entropy(lh, hd.long())
    )
    assert torch.isfinite(ce)


def test_legacy_path_untouched(corpus: Path) -> None:
    """v6 的开关不许改 legacy 行为：`action_v6=False` 时仍是 8 元组、mask 5+2。"""
    data = load_dataset(str(corpus), action_v6=True)
    ds = NNDataset(data, action_v6=False)
    item = ds[0]
    assert len(item) == 8
    assert item[5].shape == (5,) and item[6].shape == (2,)

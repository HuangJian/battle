"""v6 三头 BC 数据链（2026-11-04 落地）的接线测试。

钉三件事（都不吃真语料、峰值内存 < 20 MB）：
  1. `npyio._load_dataset_v6` 从 `a_move/a_fire/a_hold + mask.npy(9)` 拼出与 legacy 同键的 dict；
  2. `NNDataset(action_v6=True)` 返回 **10 元组**，且 mask 按 4/3/2 切成三段；
  3. 三个头各自 CE 能算，且 **move 的 target 不会越过 4 类**（`mirror_x` 那条 legacy 5 类表
     曾经把 v6 的 4 类映射出 `Target 4`）；B2 起 v6 **恢复 mirrorX 增强**（4 类表），
     本文件同时钉住「增强后标签仍在 0..3」。
  4. B2 的 class-weight 强度旋钮与 `val_loss_weighted` / `hold_acc` 回传（小语料真跑 1 epoch）。
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


def test_v6_mirror_augmentation_is_selfconsistent(corpus: Path) -> None:
    """B2：v6 增强不再被强制关闭；镜像后标签仍是 4 类，且与 `mirror_x` 同源。"""
    from worker.data.mirror import mirror_x

    data = load_dataset(str(corpus), action_v6=True)
    ds = NNDataset(data, augment=True, mirror_p=1.0, action_v6=True)
    item = ds[0]
    mv = int(data["actions"][0, 0])
    e_obs, _e_sc, e_mv, _e_ex = mirror_x(
        data["obs"][0], data["scalars"][0], mv, data["extra"][0], action_v6=True
    )
    assert int(item[3]) == e_mv
    assert np.array_equal(item[0], e_obs)
    # 4 类表：left(2)↔right(3)，up/down 不变；legacy 表会给出 4（越界）。
    assert mirror_x(data["obs"][0], data["scalars"][0], 2, None, action_v6=True)[2] == 3
    assert mirror_x(data["obs"][0], data["scalars"][0], 1, None, action_v6=True)[2] == 1


def _train_args(tmp_path: Path, **over: object):
    import types

    ns = types.SimpleNamespace(
        data_dir=str(tmp_path),
        arch="student",
        out=str(tmp_path / "w.json"),
        notes="test",
        resume=None,
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=1,
        batch=64,
        lr=1e-3,
        val_split=0.25,
        mirror_p=0.5,
        seed=1234,
        num_workers=0,
        device="cpu",
        value_coef=0.0,
        on_epoch=None,
        action_v6=True,
        policy_extra=False,
        weight_exponent=1.0,
        fire_pos_weight="auto",
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_bc_train_v6_reports_hold_and_weighted_val(corpus: Path, tmp_path: Path) -> None:
    """B2/B1：on_epoch 必须带 hold_acc；加权 val 单列且不污染 val_loss 本体。"""
    from worker.train.bc import train

    # 单 shard 微语料：1 个 train batch，只验回传字段（训练时长 <5s）。
    small = tmp_path / "one"
    _write_shard(small, "s0", 3)
    calls: list[dict] = []
    out = train(
        _train_args(
            small, out=str(tmp_path / "w.json"), on_epoch=lambda _g, _m, m: calls.append(dict(m))
        )
    )
    assert len(calls) == 1
    assert 0.0 <= calls[0]["hold_acc"] <= 1.0
    assert calls[0].get("val_loss_weighted") is not None
    assert calls[0]["val_loss_weighted"] != calls[0]["val_loss"]
    assert len(out["history"]["hold_acc"]) == 1
    assert len(out["history"]["val_loss_weighted"]) == 1


def test_bc_train_weight_exponent_zero_disables_weighting(corpus: Path, tmp_path: Path) -> None:
    """exp=0 ⇒ 不加权：加权 val 消失（两个口径重合就没必要存第二条）；hold_acc 照旧。"""
    from worker.train.bc import train

    small = tmp_path / "one0"
    _write_shard(small, "s0", 4)
    calls: list[dict] = []
    out = train(
        _train_args(
            small,
            out=str(tmp_path / "w0.json"),
            weight_exponent=0.0,
            on_epoch=lambda _g, _m, m: calls.append(dict(m)),
        )
    )
    assert calls[0].get("val_loss_weighted") is None
    assert out["history"].get("val_loss_weighted") is None
    assert calls[0].get("hold_acc") is not None


def test_legacy_path_untouched(corpus: Path) -> None:
    """v6 的开关不许改 legacy 行为：`action_v6=False` 时仍是 8 元组、mask 5+2。"""
    data = load_dataset(str(corpus), action_v6=True)
    ds = NNDataset(data, action_v6=False)
    item = ds[0]
    assert len(item) == 8
    assert item[5].shape == (5,) and item[6].shape == (2,)

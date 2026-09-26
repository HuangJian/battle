"""BC `on_epoch` 回传 + 中断接续（2026-09-13）——真训练的接续编号语义。

对应需求：bc 任务云端**每个 epoch 完成**都回传权重（hub 单文件 resume + 指标 jsonl），
中断重启从最后完成的 epoch 接续（--resume + epoch_offset），绝不从头重训。

**本文件需要真 torch**（`bc_train` 真跑 2 epoch）。纯存储/课程那 6 条 2026-09-26 分家：
  * hub 存储语义（store_bc_epoch / 租约门 / 404 语义）→ `test_bc_resume_store.py`（免 torch）；
  * 课程 `eval` 块解析 → `test_bc_course.py`（免 torch）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from schema import OBS_CHANNELS, SCALAR_DIM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_server import _JobStore
from train.bc import train as bc_train

# ------------------------------------------------------------------ hub 存储


# ------------------------------------------------------------------ bc.on_epoch（接续训练钩子）


def _make_corpus(tmp_path: Path, n: int = 16) -> Path:
    """最小语料：钩子语义只需要 on_epoch 被按全局 epoch 调用，不测收敛。

    n=16（原 60）：Windows + xdist 下 2 epoch 真 torch 训练曾 5.5–6.2s 超 5s 预算
    （同机 WSL <5s）；样本减到 1 个 batch 后 call 阶段回到 ~1–2s。语义断言不变。
    """
    rng = np.random.default_rng(7)
    d = tmp_path / "shard0"
    d.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {
        "obs": rng.integers(0, 256, (n, OBS_CHANNELS, 26, 26), dtype=np.uint8),
        "scalars": rng.standard_normal((n, SCALAR_DIM)).astype(np.float32),
        # move ∈ 0..4；fire ∈ 0..1（FIRE_DIM=2——随机 0..4 会让 masked CE 目标越界）
        "actions": np.stack(
            [rng.integers(0, 5, n), rng.integers(0, 2, n)], axis=1
        ).astype(np.int64),
        "masks": np.ones((n, 7), dtype=np.float32),
        "conditions": np.zeros(n, dtype=np.int64),
        "returns": rng.standard_normal(n).astype(np.float32),
    }
    from data.npyio import save_shard

    save_shard(str(d), arrays, {"stage": 0, "seed": 1})
    return tmp_path


def _bc_args(tmp_path: Path, **over: object) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        data_dir=str(_make_corpus(tmp_path)),
        arch="student",
        out=str(tmp_path / "bc-weights.json"),
        notes="test",
        resume=None,
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=2,
        batch=8,
        lr=1e-3,
        val_split=0.2,
        mirror_p=0.0,
        seed=1234,
        num_workers=0,
        device="cpu",
        value_coef=0.0,
        on_epoch=None,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_bc_train_on_epoch_called_per_epoch(tmp_path: Path) -> None:
    calls: list[tuple[int, dict]] = []
    ns = _bc_args(
        tmp_path, on_epoch=lambda gepoch, raw, m: calls.append((gepoch, dict(m)))
    )
    bc_train(ns)
    assert [g for g, _ in calls] == [1, 2]  # 全局 epoch 编号
    assert set(calls[0][1]) == {"train_loss", "val_loss", "move_acc", "fire_acc", "lr"}


def test_bc_train_resume_continues_epoch_numbering(tmp_path: Path) -> None:
    """中断接续语义：resume + epoch_offset=k → 下一 epoch 编号 k+1（ckpt/归档/回传
    编号连续），不是从头重训。"""
    first = _bc_args(tmp_path)
    bc_train(first)  # 2 epoch（全局 1..2）
    calls: list[int] = []
    ns = _bc_args(
        tmp_path,
        resume=str(tmp_path / "bc-weights.json"),
        epoch_offset=2,
        epochs=2,
        out=str(tmp_path / "bc-weights2.json"),
        on_epoch=lambda gepoch, raw, m: calls.append(gepoch),
    )
    bc_train(ns)
    assert calls == [3, 4]  # 接续编号，不撞第一段


# ------------------------------------------------------------------ 课程 eval 块



"""Paddle BC 双生的运行期验收（只在装了 paddle 的机器上跑）。

本机开发 venv 没有 paddle ⇒ 整个文件 skip；纯 Paddle 机（AI Studio）跑真实 1 epoch，
证明数据切分/三头 CE/权重 JSON 导出这条链在 backend=paddle 上闭合。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

paddle = pytest.importorskip("paddle")

from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM
from worker.models.student_paddle import PPOStudent, save_weights_json
from worker.train import bc_paddle
from worker.train.bc_core import SMOOTH_WINDOW, select_best_epoch


def _write_v6_shard(root, n: int = 8) -> None:
    """最小 v6 三头 shard：目标类都在 mask 内，足够跑一轮 CE/acc。"""
    rng = np.random.default_rng(11)
    root.mkdir(parents=True, exist_ok=True)
    np.save(root / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(root / "scalars.npy", rng.random((n, SCALAR_DIM), dtype=np.float32))
    np.save(root / "extra.npy", rng.random((n, POLICY_EXTRA_DIM), dtype=np.float32))
    np.save(root / "a_move.npy", np.arange(n, dtype=np.int64) % 2)
    np.save(root / "a_fire.npy", np.arange(n, dtype=np.int64) % 2)
    np.save(root / "a_hold.npy", np.arange(n, dtype=np.int64) % 2)
    mask = np.zeros((n, 9), dtype=np.float32)
    mask[:, 0:2] = 1.0
    mask[:, 4:6] = 1.0
    mask[:, 7:9] = 1.0
    np.save(root / "mask.npy", mask)
    (root / "manifest.json").write_text("{}", encoding="utf-8")


def _args(tmp_path, *, arch: str = "student", device: str = "cpu") -> SimpleNamespace:
    return SimpleNamespace(
        data_dir=str(tmp_path / "data"),
        arch=arch,
        out=str(tmp_path / "out" / "weights.json"),
        notes="paddle-bc-test",
        resume=None,
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=1,
        batch=4,
        lr=3e-3,
        val_split=0.25,
        mirror_p=0.0,
        seed=7,
        num_workers=0,
        device=device,
        value_coef=0.0,
        fire_pos_weight=0.0,
        weight_exponent=1.0,
        patience=0,
        action_v6=True,
        policy_extra=True,
    )


def test_paddle_bc_runs_one_v6_epoch_and_exports_weights(tmp_path) -> None:
    _write_v6_shard(tmp_path / "data" / "bc_s0_seed1")
    init = tmp_path / "init.json"
    save_weights_json(PPOStudent(action_v6=True, policy_extra=True), str(init))

    args = _args(tmp_path)
    args.resume = str(init)
    seen: list[tuple[int, dict]] = []
    args.on_epoch = lambda ep, _model, m: seen.append((ep, m))

    out = bc_paddle.train(args)

    assert out["sizes"]["total"] == 8
    assert out["history"]["hold_acc"] and len(out["history"]["hold_acc"]) == 1
    assert out["history"]["train_loss"] and out["history"]["val_loss"]
    assert out["delivered_epoch"] == 1
    # 返回的是**原始** val_loss，`history` 只存 round(...,4)；按同一舍入口径精确对账
    # （Paddle 真跑后暴露：原始 7.665659… vs history 7.6657 不能按 1e-6 比）。
    assert round(out["best_val_loss"], 4) == out["history"]["val_loss"][-1]
    assert seen and seen[0][0] == 1 and set(seen[0][1]) >= {"hold_acc"}
    assert (tmp_path / "out" / "weights.json").is_file()
    meta = json.loads((tmp_path / "out" / "weights.json").read_text(encoding="utf-8"))
    assert meta["arch"]["actionV6"] is True
    assert meta["arch"]["policyExtra"] is True
    assert meta["backend"] == "paddle"
    assert meta["history"]["hold_acc"] == out["history"]["hold_acc"]


def test_paddle_bc_delivers_epoch_chosen_by_smoothed_val_loss(tmp_path) -> None:
    """B1 双生：与 bc.py 同一判据——完整窗口滑动平均的最小值，不是单点 argmin。"""
    _write_v6_shard(tmp_path / "data" / "bc_s0_seed1")
    init = tmp_path / "init.json"
    save_weights_json(PPOStudent(action_v6=True, policy_extra=True), str(init))

    args = _args(tmp_path)
    args.resume = str(init)
    args.epochs = 6
    out = bc_paddle.train(args)

    delivered = select_best_epoch(out["history"]["val_loss"], window=SMOOTH_WINDOW)
    assert delivered is not None and delivered >= SMOOTH_WINDOW
    assert out["delivered_epoch"] == delivered
    # 同 `bc.py`：best_val 是原始 val_loss，history 是 4 位小数版本。
    assert round(out["best_val_loss"], 4) == out["history"]["val_loss"][delivered - 1]
    meta = json.loads((tmp_path / "out" / "weights.json").read_text(encoding="utf-8"))
    assert meta["delivered_epoch"] == delivered
    assert meta["best_smoothed_val_loss"] == pytest.approx(
        out["best_smoothed_val_loss"]
    )


def test_paddle_bc_rejects_legacy_nnpolicy(tmp_path) -> None:
    _write_v6_shard(tmp_path / "data" / "bc_s0_seed1")
    with pytest.raises(ValueError, match="只支持 --arch student"):
        bc_paddle.train(_args(tmp_path, arch="bc"))


def test_paddle_bc_rejects_torch_only_device(tmp_path) -> None:
    _write_v6_shard(tmp_path / "data" / "bc_s0_seed1")
    with pytest.raises(ValueError, match="torch 专属"):
        bc_paddle.train(_args(tmp_path, device="cuda-dp"))


def test_paddle_masked_ce_drops_rows_with_fewer_than_two_legal_classes() -> None:
    """行选择与 torch 版同语义：0/1 个合法类的样本整行剔除，2+ 保留（`_kept` 升序 gather）。

    Paddle 3.3 的布尔下标 `x[keep]` 在 C++ 里对未初始化 Tensor 调 `reshape`，每进程打一条
    `tensor.cc:111` 弃用警告（BC 训练日志里那条）；implementation 改走 `nonzero` + `gather`，
    本用例就钉住「同序、同值」这条等价前提。
    """
    logits = np.array(
        [[2.0, 0.5, -1.0], [0.1, 0.2, 0.3], [0.0, 0.0, 0.0], [-0.5, 1.5, 0.25]],
        dtype=np.float32,
    )
    mask = np.array([[1, 1, 1], [1, 0, 0], [0, 0, 0], [1, 1, 0]], dtype=np.float32)
    target = np.array([2, 1, 0, 1], dtype=np.int64)

    loss = bc_paddle._masked_ce(
        paddle.to_tensor(logits), paddle.to_tensor(target), paddle.to_tensor(mask)
    )
    acc = bc_paddle._masked_acc(
        paddle.to_tensor(logits), paddle.to_tensor(target), paddle.to_tensor(mask)
    )

    z = np.where(mask > 0, logits, -1e9).astype(np.float32)
    z = z - z.max(axis=1, keepdims=True)
    logp = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
    kept = mask.sum(axis=1) >= 2
    ref = float((-logp[np.arange(len(target)), target])[kept].mean())
    assert float(loss) == pytest.approx(ref, rel=1e-5)
    # 保留行：row0 预测 0（错）、row3 预测 1（对）⇒ 0.5。
    assert acc == pytest.approx(0.5)

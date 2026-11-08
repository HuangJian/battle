"""P2-B `--train-heads policy`：只训 move/fire/hold 头，trunk 与 value_head 逐字节不变。

背景（2026-11-08 w2-pickup-ft）：pickup 窗口 1 万步 / 3 epoch 的全参微调把 trunk
拉偏 —— kills −2.8、base +25pp。冻结 trunk 才能只改「捡」不动「打」。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from worker.data.weights_io import save_weights_json
from worker.models.student import PPOStudent
from worker.train.bc import TRAIN_HEAD_PREFIXES, _freeze_except, train

N = 48
CH, BOARD = 17, 26
SCALAR_DIM, EXTRA_DIM = 46, 57
TRUNK_PREFIXES = ("stem.", "blocks.", "fc.")


def _write_shard(root: Path, name: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "obs.npy", rng.integers(0, 256, (N, CH, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((N, SCALAR_DIM)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((N, EXTRA_DIM)).astype(np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 4, N, dtype=np.uint8))
    np.save(d / "a_fire.npy", rng.integers(0, 3, N, dtype=np.uint8))
    np.save(d / "a_hold.npy", rng.integers(0, 2, N, dtype=np.uint8))
    np.save(d / "mask.npy", np.ones((N, 9), dtype=np.uint8))
    (d / "manifest.json").write_text(
        json.dumps({"outcome": "stage_clear", "samples": N}), encoding="utf-8"
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    for i in range(4):
        _write_shard(tmp_path, f"rl_s2000_seed{i}", 200 + i)
    return tmp_path


def _args(corpus: Path, init: Path, out: Path, **over: object) -> SimpleNamespace:
    base = dict(
        data_dir=str(corpus),
        arch="student",
        out=str(out),
        notes="p2b policy-heads unit",
        resume=str(init),
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=1,
        batch=32,
        lr=1e-3,
        val_split=0.25,
        mirror_p=0.0,
        seed=1234,
        num_workers=0,
        device="cpu",
        value_coef=0.0,
        policy_coef=1.0,
        train_heads="policy",
        fire_pos_weight="auto",
        weight_exponent=0.0,
        patience=0,
        action_v6=True,
        policy_extra=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_policy_heads_only_preserves_trunk_and_value(corpus: Path, tmp_path: Path) -> None:
    init = tmp_path / "init.json"
    torch.manual_seed(11)
    model = PPOStudent(action_v6=True, policy_extra=True)
    save_weights_json(model, str(init), extra_meta={"arch": model.arch()})
    before = json.loads(init.read_text(encoding="utf-8"))["params"]

    res = train(_args(corpus, init, tmp_path / "weights.json"))
    after = json.loads(Path(res["out"]).read_text(encoding="utf-8"))["params"]

    heads = TRAIN_HEAD_PREFIXES["policy"]
    changed = []
    for name, entry in before.items():
        if name.startswith(heads):
            if after[name]["data"] != entry["data"]:
                changed.append(name)
            continue
        assert after[name]["data"] == entry["data"], f"非 policy 头参数被改动: {name}"
    assert changed, "三个策略头都没有更新——policy-heads 没有真正训练"
    assert all(any(name.startswith(h) for h in heads) for name in changed)


def test_train_heads_value_conflicts_with_policy_coef(corpus: Path, tmp_path: Path) -> None:
    init = tmp_path / "init.json"
    torch.manual_seed(12)
    save_weights_json(
        PPOStudent(action_v6=True, policy_extra=True),
        str(init),
        extra_meta={"arch": "student"},
    )
    with pytest.raises(ValueError, match="train_heads=value"):
        train(_args(corpus, init, tmp_path / "w.json", train_heads="value", policy_coef=1.0))


def test_train_heads_policy_conflicts_with_zero_policy_coef(
    corpus: Path, tmp_path: Path
) -> None:
    init = tmp_path / "init.json"
    torch.manual_seed(13)
    save_weights_json(
        PPOStudent(action_v6=True, policy_extra=True),
        str(init),
        extra_meta={"arch": "student"},
    )
    with pytest.raises(ValueError, match="train_heads=policy"):
        train(_args(corpus, init, tmp_path / "w.json", train_heads="policy", policy_coef=0.0))


def test_freeze_except_keeps_only_policy_heads() -> None:
    model = PPOStudent(action_v6=True)
    _freeze_except(model, TRAIN_HEAD_PREFIXES["policy"])
    heads = TRAIN_HEAD_PREFIXES["policy"]
    for name, p in model.named_parameters():
        assert p.requires_grad is name.startswith(heads), name

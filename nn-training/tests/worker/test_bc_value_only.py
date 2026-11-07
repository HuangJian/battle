"""C9 value-only BC（plan/classic35-path.plan.md §5 C9）。

判据不是「loss 降了」，而是**策略逐字节不变**：
  · `--policy-coef 0` 冻结所有非 `value_head.*` 参数（含共享 trunk）；
  · optimizer 只拿 value_head 参数 ⇒ AdamW weight_decay 也碰不到策略；
  · 同一 checkpoint 走一遍 1-epoch value-only，导出 JSON 的每个非 value 参数
    必须与输入 base64 完全一致，value_head 必须有更新。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from worker.bc_config import load_bc_course
from worker.data.npyio import load_dataset
from worker.data.weights_io import save_weights_json
from worker.models.student import PPOStudent
from worker.train.bc import _freeze_non_value, train

N = 48
CH, BOARD = 17, 26
SCALAR_DIM, EXTRA_DIM = 46, 57


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
    # value-only 必须有有效 return；随机值足够验证「只更新 value 头」。
    np.save(d / "returns.npy", rng.standard_normal(N).astype(np.float32))
    (d / "manifest.json").write_text(
        json.dumps({"outcome": "stage_clear", "samples": N}), encoding="utf-8"
    )


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    for i in range(4):
        _write_shard(tmp_path, f"rl_s2000_seed{i}", 100 + i)
    return tmp_path


def _args(corpus: Path, init: Path, out: Path, **over: object) -> SimpleNamespace:
    base = dict(
        data_dir=str(corpus),
        arch="student",
        out=str(out),
        notes="c9 value-only unit",
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
        value_coef=1.0,
        policy_coef=0.0,
        fire_pos_weight="auto",
        weight_exponent=0.0,
        patience=0,
        action_v6=True,
        policy_extra=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_v6_loader_reads_optional_returns(corpus: Path) -> None:
    data = load_dataset(str(corpus), action_v6=True)
    assert data["returns"].shape == (N * 4,)
    assert np.isfinite(data["returns"]).all()


def test_value_only_preserves_every_non_value_parameter(corpus: Path, tmp_path: Path) -> None:
    init = tmp_path / "init.json"
    torch.manual_seed(7)
    model = PPOStudent(action_v6=True, policy_extra=True)
    save_weights_json(model, str(init), extra_meta={"arch": model.arch()})
    before = json.loads(init.read_text(encoding="utf-8"))["params"]

    res = train(_args(corpus, init, tmp_path / "weights.json"))
    after = json.loads(Path(res["out"]).read_text(encoding="utf-8"))["params"]

    changed = []
    for name, entry in before.items():
        if name.startswith("value_head."):
            if after[name]["data"] != entry["data"]:
                changed.append(name)
            continue
        assert after[name]["data"] == entry["data"], f"策略/主干参数被改动: {name}"
    assert changed, "value_head 没有任何更新——value-only 没有真正训练"


def test_freeze_non_value_keeps_only_value_head_trainable() -> None:
    model = PPOStudent(action_v6=True)
    _freeze_non_value(model)
    for name, p in model.named_parameters():
        assert p.requires_grad is name.startswith("value_head."), name


def test_bc_course_policy_coef_defaults_to_one_and_reaches_manifest() -> None:
    from trainer import bc_loop

    c = load_bc_course("bc-c4-v3")
    assert c.train.policy_coef == 1.0
    assert bc_loop.bc_job_extra(c, 1)["policy_coef"] == 1.0

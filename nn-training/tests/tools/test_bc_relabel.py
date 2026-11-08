"""`tools/bc_relabel.py` 的契约（P2-B 跨教师标注）。

钉四件事：动作三头被重写成教师 argmax、`lp_*` 与 `a_*` 同值、其余文件原样复制、
`manifest` 追加 relabel 块且不改原字段；`returns.npy` 缺失不凭空造。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from tools.bc_relabel import find_shards, relabel_shard
from tools.p0_teacher_agreement import load_policy_model
from worker.data.weights_io import save_weights_json
from worker.models.student import PPOStudent

N = 4


@pytest.fixture()
def teacher(tmp_path: Path) -> Path:
    """现造一个 v6 教师权重：测试不依赖仓库里的 `weights/`（gitignore，CI 没有）。"""
    torch.manual_seed(21)
    p = tmp_path / "teacher.json"
    m = PPOStudent(action_v6=True, policy_extra=True)
    save_weights_json(m, str(p), extra_meta={"arch": m.arch()})
    return p


def _write_shard(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    np.save(d / "obs.npy", np.zeros((N, 17, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", np.zeros((N, 46), dtype=np.float32))
    np.save(d / "extra.npy", np.zeros((N, 57), dtype=np.float32))
    np.save(d / "mask.npy", np.ones((N, 9), dtype=np.uint8))
    for f in ("a_move", "a_fire", "a_hold", "lp_move", "lp_fire", "lp_hold"):
        np.save(d / f"{f}.npy", np.zeros((N,), dtype=np.uint8))
    np.save(d / "done.npy", np.zeros((N,), dtype=np.int64))
    np.save(d / "metrics.npy", np.zeros((N + 1, 90), dtype=np.float32))
    (d / "manifest.json").write_text(
        json.dumps({"stage": 16, "seed": 0, "nSamples": N, "outcome": "lives_exhausted"}),
        encoding="utf-8",
    )
    return d


def test_relabel_rewrites_actions(tmp_path: Path, teacher: Path) -> None:
    src = _write_shard(tmp_path / "in", "rl_s16_seed0")
    dst = tmp_path / "out" / "rl_s16_seed0"
    model = load_policy_model(str(teacher))
    n = relabel_shard(str(src), str(dst), model)
    assert n == N

    for f in ("a_move", "a_fire", "a_hold", "lp_move", "lp_fire", "lp_hold"):
        a = np.load(dst / f"{f}.npy")
        assert a.shape == (N,) and a.dtype == np.uint8, f
    assert int(np.load(dst / "a_move.npy").max()) <= 3
    assert int(np.load(dst / "a_fire.npy").max()) <= 2
    assert int(np.load(dst / "a_hold.npy").max()) <= 1
    assert np.array_equal(np.load(dst / "a_move.npy"), np.load(dst / "lp_move.npy"))

    man = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    assert man["relabel"]["nSamples"] == N
    assert man["stage"] == 16 and man["outcome"] == "lives_exhausted"
    for f in ("obs.npy", "scalars.npy", "extra.npy", "mask.npy", "done.npy", "metrics.npy"):
        assert (dst / f).exists(), f
    assert not (dst / "returns.npy").exists(), "源没有 returns 就不该造"


def test_find_shards_requires_obs_and_mask(tmp_path: Path) -> None:
    _write_shard(tmp_path / "in", "rl_s16_seed0")
    bad = tmp_path / "in" / "no_mask"
    bad.mkdir()
    np.save(bad / "obs.npy", np.zeros((1, 17, 26, 26), dtype=np.uint8))
    assert find_shards(str(tmp_path / "in"), ("obs.npy", "mask.npy", "manifest.json")) == [
        str(tmp_path / "in" / "rl_s16_seed0")
    ]


def test_empty_shard_is_loud(tmp_path: Path, teacher: Path) -> None:
    src = _write_shard(tmp_path / "in", "rl_s16_seed1")
    np.save(src / "obs.npy", np.zeros((0, 17, 26, 26), dtype=np.uint8))
    model = load_policy_model(str(teacher))
    with pytest.raises(ValueError):
        relabel_shard(str(src), str(tmp_path / "out2"), model)

"""test_reward_npy_shard —— 只有 `reward.npy` 的 shard 的装载判据（wire-diet §3 P1）。

奖励现在在**打包端**（hub）算，节点只直读 —— 所以判据全压在「这份 reward 是不是本轮的」：
`reward_meta.json` 的 it / metrics_version / formula_hash 任一条不符都必须**响亮**
（`ProtocolError`，内容决定性、不重认领）。数值上「it 用错」与正常 reward 无从区分，
这四条用例就是那条判据的全部守卫（评审 P1-4）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.reward_context import Scoped, reset
from biz.reward_library import METRICS_VERSION
from common.errors import ProtocolError
from common.payload import DROP_MEMBER, pack_payload, unpack_payload
from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM
from common.shard import REWARD_META_NAME, REWARD_NAME
from worker.ppo.np_core import discover_rl_shards, load_shard

IT = 7
FH = "f" * 16


@pytest.fixture(autouse=True)
def _ctx_reset() -> Iterator[None]:
    reset()
    yield
    reset()


def _write_reward_shard(
    root: Path,
    name: str,
    n: int,
    *,
    reward: np.ndarray | None = None,
    meta: dict | None = None,
    keep_meta: bool = True,
    keep_metrics: bool = False,
) -> Path:
    """只带 `reward.npy` 的 per-tick shard（+ 血缘 meta）。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(len(name) * 13 + n)
    np.save(d / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", (rng.random((n, SCALAR_DIM)).astype(np.float32) * 3 - 1.5))
    np.save(d / "extra.npy", rng.random((n, POLICY_EXTRA_DIM)).astype(np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 5, n))
    np.save(d / "a_fire.npy", rng.integers(0, 2, n))
    np.save(d / "lp_move.npy", rng.random(n).astype(np.float32) - 2)
    np.save(d / "lp_fire.npy", rng.random(n).astype(np.float32) - 2)
    np.save(d / "value.npy", rng.random(n).astype(np.float32) * 0.1)
    np.save(d / "done.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 7), dtype=np.int64))
    if keep_metrics:
        np.save(d / "metrics.npy", rng.random((n + 1, 76), dtype=np.float64))
    np.save(d / REWARD_NAME, reward if reward is not None else rng.random(n).astype(np.float32))
    if keep_meta:
        # `meta` 是**覆盖项**（合并到缺省之上）：每条用例只改它要探的那一栏，其余保持合法
        # ——否则「改 it 探 it」会先撞上别的栏，用例名字与它真正钉住的判据就分家了。
        got = {
            "reward_version": 1,
            "it": IT,
            "metrics_version": METRICS_VERSION,
            "formula_hash": FH,
            **(meta or {}),
        }
        (d / REWARD_META_NAME).write_text(json.dumps(got), encoding="utf-8")
    (d / "manifest.json").write_text(
        json.dumps({"wver": "w" * 8, "stage": 2000, "seed": n}), encoding="utf-8"
    )
    return d


def _holder(it: int = IT, formula_hash: str = FH) -> Scoped:
    """与 `remote/train_core.py` 同源的 holder（`identity` 里带 formula_hash）。"""
    return Scoped(
        reward_fn=None,
        gamma=0.995,
        lam=0.95,
        it=it,
        metrics_version=METRICS_VERSION,
        identity={"course": "s4b", "formula_hash": formula_hash},
    )


def test_reward_path_needs_no_formula(tmp_path: Path) -> None:
    """直读路径**不需要** reward_fn（公式只在打包端用）——但 meta 必须齐全。"""
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 9)
    want = np.load(d / REWARD_NAME)
    with _holder():
        got = load_shard(str(d))
    np.testing.assert_array_equal(got["reward"], want)
    assert got["reward"].dtype == np.float32
    assert int(got["stage"]) == 2000


def test_it_mismatch_loud(tmp_path: Path) -> None:
    """★ it 不符 = 打包端用错了迭代号（评审 P1-4 的唯一暴露面）⇒ 拒绝，不静默。"""
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, meta={"it": 1})
    with _holder(it=IT), pytest.raises(ProtocolError, match="it=1"):
        load_shard(str(d))


def test_reward_version_mismatch_loud(tmp_path: Path) -> None:
    """奖励口径版本不符 ⇒ 拒收：节点 code.zip 与 hub 不同代时，两版算式产出的 reward
    在数值/形状上都无从分辨（打包端那侧写的就是本仓常量，所以差异只来自代际差）。"""
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, meta={"it": IT, "reward_version": 0})
    with _holder(), pytest.raises(ProtocolError, match="reward_version"):
        load_shard(str(d))


def test_metrics_version_mismatch_loud(tmp_path: Path) -> None:
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, meta={"it": IT, "metrics_version": 1})
    with _holder(), pytest.raises(ProtocolError, match="metrics_version"):
        load_shard(str(d))


def test_formula_hash_mismatch_loud(tmp_path: Path) -> None:
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, meta={"it": IT, "formula_hash": "x" * 16})
    with _holder(), pytest.raises(ProtocolError, match="formula_hash"):
        load_shard(str(d))


def test_missing_meta_loud(tmp_path: Path) -> None:
    """没有 meta ⇒ 无从判定它是哪一轮算的 ⇒ 拒绝（不是「信任字节」）。"""
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, keep_meta=False)
    with _holder(), pytest.raises(ProtocolError, match=REWARD_META_NAME):
        load_shard(str(d))


def test_shape_mismatch_loud(tmp_path: Path) -> None:
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, reward=np.zeros(4, dtype=np.float32))
    with _holder(), pytest.raises(ValueError, match="形状"):
        load_shard(str(d))


def test_non_finite_loud(tmp_path: Path) -> None:
    bad = np.zeros(5, dtype=np.float32)
    bad[2] = np.nan
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, reward=bad)
    with _holder(), pytest.raises(ProtocolError, match="非有限"):
        load_shard(str(d))


def test_metrics_still_wins_when_present(tmp_path: Path) -> None:
    """两条都在盘上时走**旧路径**（metrics 优先）——旧行为逐字节不变。"""
    d = _write_reward_shard(tmp_path, "rl_s2000_seed1", 5, keep_metrics=True)
    holder = Scoped(
        reward_fn=lambda m, outcome, score, it: np.zeros(m.shape[0] - 1, dtype=np.float64),
        gamma=0.995,
        lam=0.95,
        it=IT,
        metrics_version=METRICS_VERSION,
        identity={"course": "s4b", "formula_hash": FH},
    )
    with holder:
        got = load_shard(str(d))
    np.testing.assert_array_equal(got["reward"], np.zeros(5, dtype=np.float32))


def test_discover_accepts_either_form(tmp_path: Path) -> None:
    """发现判据：obs + （metrics **或** reward）才算 shard；只有 obs 的目录不算。"""
    _write_reward_shard(tmp_path, "rl_s2000_seed1", 3)
    _write_reward_shard(tmp_path, "rl_s2000_seed2", 3, keep_metrics=True)
    lonely = tmp_path / "rl_s2000_seed3"
    lonely.mkdir()
    np.save(lonely / "obs.npy", np.zeros((3, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    got = discover_rl_shards(str(tmp_path))
    assert [Path(p).name for p in got] == ["rl_s2000_seed1", "rl_s2000_seed2"]


def test_round_trip_through_a_packed_payload(tmp_path: Path) -> None:
    """走一次**真实解包产物**（不是手搓目录）：drop metrics + 加 reward/meta 的字节真能装回。"""
    src = tmp_path / "src"
    d = _write_reward_shard(src, "rl_s2000_seed1", 6)
    out = tmp_path / "p.tar.xz"
    pack_payload(
        [d],
        {},
        out,
        member_bytes=lambda _d, f: DROP_MEMBER if f == REWARD_META_NAME else None,
        shard_extra=lambda _d: {},
    )
    _m, dirs = unpack_payload(out, tmp_path / "un")
    with _holder(), pytest.raises(ProtocolError, match=REWARD_META_NAME):
        load_shard(str(dirs[0]))

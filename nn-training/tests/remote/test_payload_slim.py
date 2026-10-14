"""test_payload_slim —— wire-diet（plan/wire-diet.plan.md §3 M1+M2）的契约测试。

钉住五件事（每条对应评审 `plan/wire-diet.review-bf.md` 的一条判据）：

  ① **往返**：DROP 的成员真不在、替换的成员是 f16、`manifest.json` 与 `value.npy` 逐字节不变、
     非 per-tick 目录（无 `metrics.npy`）整目录不动；
  ② **决定性**：同一输入打两次**逐字节相同** —— 这是「变换走内存字节而不是临时文件」的判据
     （临时文件的 mtime 会进 tar 头，同一份内容每打一次 `payload_sha256` 都变）；
  ③ **reward 等价**：打包端算出的 reward == 节点按 `metrics.npy` 算出的 reward（逐元素相同）
     —— 这是 P1「无损」的全部含义；
  ④ **回退**：无 `reward_spec` ⇒ 照旧带 `metrics.npy`（旧行为），P2 的 f16 仍生效；
  ⑤ **判据同源**：`RL_F16_FIELDS` 与装载规格 `_RL_SHARD_SPEC` 一致（加字段只改一边 ⇒ 这里红）。
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
from biz.reward_library import METRICS_DIM, METRICS_VERSION, build_reward_fn, reward_from_spec
from common.errors import ProtocolError
from common.payload import DROP_MEMBER, pack_payload, unpack_payload
from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM

# 常量与读端同源（住 `common.shard`，写端/读端共用一份定义）。
from common.shard import REWARD_META_NAME, REWARD_NAME, RL_F16_FIELDS
from remote.payload_slim import F16_FILES, build_slim
from worker.config import load_course
from worker.ppo.np_core import _RL_SHARD_SPEC, load_shard

COURSE = "s4b"
IT = 7


@pytest.fixture(autouse=True)
def _ctx_reset() -> Iterator[None]:
    reset()
    yield
    reset()


def _synthetic_metrics(n: int, seed: int = 3) -> np.ndarray:
    """n 决策行 + 1 终局行。只求**非平凡且有限**（等价性判据不需要列语义）。"""
    rng = np.random.default_rng(seed)
    return rng.random((n + 1, METRICS_DIM), dtype=np.float64) * 3.0


def _write_rl_shard(
    root: Path, name: str, n: int, *, metrics_version: int | None = METRICS_VERSION
) -> Path:
    """合成一个 per-tick shard 目录（文件名与 `export-rl-rollout.ts` 一致）。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(len(name) * 17 + n)
    np.save(d / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", (rng.random((n, SCALAR_DIM)).astype(np.float32) * 3 - 1.5))
    np.save(d / "extra.npy", rng.random((n, POLICY_EXTRA_DIM)).astype(np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 5, n))
    np.save(d / "a_fire.npy", rng.integers(0, 2, n))
    np.save(d / "lp_move.npy", rng.random(n).astype(np.float32) - 2)
    np.save(d / "lp_fire.npy", rng.random(n).astype(np.float32) - 2)
    np.save(d / "value.npy", rng.random(n).astype(np.float32) * 0.1)
    np.save(d / "metrics.npy", _synthetic_metrics(n, seed=n))
    np.save(d / "done.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 7), dtype=np.int64))
    man: dict[str, object] = {
        "wver": "w" * 8,
        "stage": 2000,
        "seed": n,
        "nSamples": n,
        "outcome": "timeout",
        "score": 0.25,
        "metrics_version": metrics_version,
    }
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def _spec():
    return load_course(COURSE).reward_spec()


def _slim(**kw):
    return build_slim(reward_spec=_spec(), it=IT, metrics_version=METRICS_VERSION, **kw)


def _pack(shard_dirs: list[Path], out: Path, slim=None) -> str:
    if slim is None:
        return pack_payload(shard_dirs, {}, out)
    return pack_payload(
        shard_dirs, {}, out, member_bytes=slim.member_bytes, shard_extra=slim.shard_extra
    )


def _holder(it: int = IT):
    """与 `remote/train_core.py` 同源的 holder（it/metrics_version 来自 job manifest）。"""
    return Scoped(
        reward_fn=build_reward_fn(_spec()),
        gamma=0.995,
        lam=0.95,
        it=it,
        metrics_version=METRICS_VERSION,
        identity={"course": COURSE, "formula_hash": _spec().identity()},
    )


# --------------------------------------------------------------- ① 往返 + ⑤ 判据同源
def test_round_trip_drops_metrics_and_ships_reward(tmp_path: Path) -> None:
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 12)
    before = {p.name: p.read_bytes() for p in sorted(shard.iterdir())}
    out = tmp_path / "payload.tar.xz"
    slim = _slim()
    _pack([shard], out, slim)
    _m, dirs = unpack_payload(out, tmp_path / "un")
    assert len(dirs) == 1
    got = Path(dirs[0])

    assert not (got / "metrics.npy").exists(), "P1：metrics.npy 必须不进 payload"
    assert (got / REWARD_NAME).exists() and (got / REWARD_META_NAME).exists()
    r = np.load(got / REWARD_NAME)
    assert r.dtype == np.float32 and r.shape == (12,), f"reward 形状/ dtype 不对：{r.shape}"
    meta = json.loads((got / REWARD_META_NAME).read_text(encoding="utf-8"))
    assert meta == {
        "reward_version": 1,
        "it": IT,
        "metrics_version": METRICS_VERSION,
        "formula_hash": _spec().identity(),
    }
    # manifest.json 与 value.npy 逐字节不变（血缘/GAE 基线的判据面不许动）
    assert (got / "manifest.json").read_bytes() == before["manifest.json"]
    assert (got / "value.npy").read_bytes() == before["value.npy"]
    assert np.load(got / "value.npy").dtype == np.float32
    # f16 字段：盘上是 f16，加载端 astype 回 f32 的绝对误差 ≤ 实测上界（2 字节舍入 ⇒ 相对 2^-11）
    for fname in ("scalars.npy", "extra.npy", "lp_move.npy", "lp_fire.npy"):
        raw = np.load(got / fname)
        assert raw.dtype == np.float16, f"{fname} 应为 f16"
        ref = np.load(shard / fname).astype(np.float32)
        err = np.abs(raw.astype(np.float32) - ref).max()
        assert err <= np.abs(ref).max() * 2**-11 + 1e-7, f"{fname} f16 误差超界：{err}"
    assert not (got / "obs.npy").is_file() or np.load(got / "obs.npy").dtype == np.uint8
    assert slim.fmt == "reward+f16", slim.fmt_line()


def test_only_adds_what_the_reader_contract_names(tmp_path: Path) -> None:
    """⑤ 加字段/改名只改一边 ⇒ 这里红：`RL_F16_FIELDS` 必须是装载规格里的 **f32** 字段。"""
    for key in RL_F16_FIELDS:
        assert key in _RL_SHARD_SPEC, f"{key} 不在 _RL_SHARD_SPEC：{sorted(_RL_SHARD_SPEC)}"
        fname, dtype = _RL_SHARD_SPEC[key]
        assert fname == f"{key}.npy", f"{key} 的文件名约定变了：{fname}"
        assert np.dtype(dtype) == np.float32, f"{key} 不是 f32（f16 变换的前提没了）"
        assert fname in F16_FILES
    assert "value" not in RL_F16_FIELDS, "value 是 GAE 基线，明确不在 f16 表内（§3 P2）"


def test_non_rl_dir_is_untouched(tmp_path: Path) -> None:
    """没有 metrics.npy 的目录（BC / intent）整目录原样：不猜、不量化。"""
    d = tmp_path / "src" / "bc_s1_seed1"
    d.mkdir(parents=True)
    rng = np.random.default_rng(1)
    np.save(d / "obs.npy", rng.integers(0, 256, (4, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.random((4, SCALAR_DIM)).astype(np.float32))
    (d / "manifest.json").write_text(json.dumps({"wver": "w", "stage": 1, "seed": 1}), "utf-8")
    out = tmp_path / "payload.tar.xz"
    slim = _slim()
    _pack([d], out, slim)
    _m, dirs = unpack_payload(out, tmp_path / "un")
    got = Path(dirs[0])
    assert np.load(got / "scalars.npy").dtype == np.float32
    assert slim.fmt == "" and slim.n_untouched_dirs == 1


def test_already_new_form_untouched(tmp_path: Path) -> None:
    """盘上已有 reward.npy（历史/重打）⇒ 整目录不动（幂等：重打不叠一次量化）。"""
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 4)
    np.save(shard / REWARD_NAME, np.random.default_rng(5).random(4).astype(np.float32))
    slim = _slim()
    _pack([shard], tmp_path / "p.tar.xz", slim)
    _m, dirs = unpack_payload(tmp_path / "p.tar.xz", tmp_path / "un")
    got = Path(dirs[0])
    assert (got / "metrics.npy").exists(), "已是新形态 ⇒ 不动（metrics 照旧在）"
    assert slim.n_untouched_dirs == 1 and slim.fmt == "", slim.fmt_line()


# --------------------------------------------------------------- ② 决定性
def test_pack_is_deterministic(tmp_path: Path) -> None:
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 9)
    a = _pack([shard], tmp_path / "a.tar.xz", _slim())
    b = _pack([shard], tmp_path / "b.tar.xz", _slim())
    assert a == b, "同一输入两次打包必须逐字节相同（内存变换，不是临时文件）"


def test_hook_default_is_byte_identical_to_old_behaviour(tmp_path: Path) -> None:
    """缺省调用形状不变：no-op 钩子（全部 None/空）与不传钩子逐字节相同。"""
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 5)
    old = _pack([shard], tmp_path / "old.tar.xz", None)
    noop = pack_payload(
        [shard],
        {},
        tmp_path / "noop.tar.xz",
        member_bytes=lambda _d, _f: None,
        shard_extra=lambda _d: {},
    )
    assert old == noop


def test_extra_member_collision_loud(tmp_path: Path) -> None:
    """同名 arcname = 由解包顺序决定吃哪份（偶然语义）⇒ 响亮拒绝，不打进归档。"""
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 3)
    with pytest.raises(ProtocolError, match="同名"):
        pack_payload([shard], {}, tmp_path / "x.tar.xz", shard_extra=lambda _d: {"obs.npy": b"x"})


def test_drop_sentinel_is_distinct_from_none(tmp_path: Path) -> None:
    """三种结局分列：DROP 真丢，None 真留（不是同一个返回值）。"""
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 3)
    pack_payload(
        [shard],
        {},
        tmp_path / "d.tar.xz",
        member_bytes=lambda _d, f: DROP_MEMBER if f == "a_move.npy" else None,
    )
    _m, dirs = unpack_payload(tmp_path / "d.tar.xz", tmp_path / "un")
    got = Path(dirs[0])
    assert not (got / "a_move.npy").exists()
    assert (got / "a_fire.npy").exists()


# --------------------------------------------------------------- ③ reward 等价
def test_reward_equals_the_metrics_path(tmp_path: Path) -> None:
    """打包端算的 reward == 节点按 metrics 算的 reward（P1「无损」的全部含义）。"""
    src = tmp_path / "src"
    n = 11
    shard = _write_rl_shard(src, "rl_s2000_seed1", n)
    _pack([shard], tmp_path / "p.tar.xz", _slim())
    _m, dirs = unpack_payload(tmp_path / "p.tar.xz", tmp_path / "un")
    with _holder():
        want = load_shard(str(shard))["reward"]  # 旧路径：公式引擎按 metrics 算
        got = load_shard(str(dirs[0]))["reward"]  # 新路径：直读打包端算好的 reward
    assert np.any(want != 0.0), "合成数据退化（全 0）会让等价性变成空断言"
    np.testing.assert_array_equal(got, want)
    # 与打包端的算式同源（同一 reward_from_spec + 同一缺省）
    m = np.load(shard / "metrics.npy")
    man = json.loads((shard / "manifest.json").read_text(encoding="utf-8"))
    direct = np.asarray(
        reward_from_spec(_spec(), m, man["outcome"], man["score"], IT), dtype=np.float32
    )
    np.testing.assert_array_equal(got, direct)


# --------------------------------------------------------------- ④ 回退
def test_without_reward_spec_keeps_metrics_but_still_f16(tmp_path: Path) -> None:
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 6)
    slim = build_slim(reward_spec=None, it=IT, metrics_version=METRICS_VERSION)
    _pack([shard], tmp_path / "p.tar.xz", slim)
    _m, dirs = unpack_payload(tmp_path / "p.tar.xz", tmp_path / "un")
    got = Path(dirs[0])
    assert (got / "metrics.npy").exists(), "缺 reward_spec ⇒ P1 不做（照旧带 metrics）"
    assert not (got / REWARD_NAME).exists()
    assert np.load(got / "scalars.npy").dtype == np.float16, "P2 与 P1 正交，仍生效"
    assert slim.fmt == "f16", slim.fmt_line()


def test_metrics_version_mismatch_loud(tmp_path: Path) -> None:
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 4, metrics_version=METRICS_VERSION - 1)
    with pytest.raises(ProtocolError, match="metrics_version"):
        _pack([shard], tmp_path / "p.tar.xz", _slim())


def test_row_mismatch_loud(tmp_path: Path) -> None:
    """metrics 行数 != obs 行数 + 1 ⇒ 打包前就响（今天要等上云才炸）。"""
    src = tmp_path / "src"
    shard = _write_rl_shard(src, "rl_s2000_seed1", 4)
    np.save(shard / "metrics.npy", _synthetic_metrics(3))  # 少一行
    with pytest.raises(ProtocolError, match="行数"):
        _pack([shard], tmp_path / "p.tar.xz", _slim())

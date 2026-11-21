"""`tools/build_demo_bank.py`：当前 schema 维度从 schema 读，旧目录响亮拒绝。

为什么单独一条：这个脚本已经漂过一次（POLICY_EXTRA 9→37 仍硬编码 9）。
本用例同时钉住三件事：
  · 当前 shard（46 scalars / 57 extra / obsSchemaMajor=OBS_SCHEMA_MAJOR）能建出正确的 bank；
  · v4 旧 extra（9 维）/ v3 旧 scalars（30 维）不许静默入库；
  · manifest 的 obsSchemaMajor < 当前 schema 不许入库（防拿旧目录建库）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from common.schema import BOARD, OBS_CHANNELS, OBS_SCHEMA_MAJOR, POLICY_EXTRA_DIM, SCALAR_DIM


def _load_tool() -> Any:
    """按文件路径加载（tools/ 无 __init__.py；与 test_course_compare 同款）。"""
    spec = importlib.util.spec_from_file_location(
        "_build_demo_bank_under_test", NN_ROOT / "tools" / "build_demo_bank.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


TOOL = _load_tool()


def _write_shard(
    root: Path,
    name: str,
    *,
    n: int = 3,
    scalars: int = SCALAR_DIM,
    extra: int = POLICY_EXTRA_DIM,
    obs_schema_major: int = OBS_SCHEMA_MAJOR,
    stage: int = 0,
    outcome: str = "stage_clear",
    v6: bool = False,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    np.save(d / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((n, scalars)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((n, extra)).astype(np.float32))
    if v6:
        np.save(d / "a_move.npy", rng.integers(0, 4, n, dtype=np.uint8))
        np.save(d / "a_fire.npy", rng.integers(0, 3, n, dtype=np.uint8))
        np.save(d / "a_hold.npy", rng.integers(0, 2, n, dtype=np.uint8))
        np.save(d / "mask.npy", np.ones((n, 9), dtype=np.uint8))
    else:
        np.save(d / "actions.npy", rng.integers(0, 2, (n, 2), dtype=np.uint8))
        np.save(d / "masks.npy", np.ones((n, 7), dtype=np.uint8))
    # manifest 与真 shard 同形：**必须带 `stage`**（分层抽样按关配额）与 `outcome`
    # （防守块默认只取 stage_clear）。少这两个键 = 真语料能建、夹具建不了 ⇒ 假绿。
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "schemaMajor": obs_schema_major,
                "obsSchemaMajor": obs_schema_major,
                "shard": name,
                "nSamples": n,
                "stage": stage,
                "seed": 1,
                "outcome": outcome,
            }
        ),
        encoding="utf-8",
    )
    return d


def _run(monkeypatch: pytest.MonkeyPatch, corpus: Path, out: Path) -> None:
    monkeypatch.setattr(
        sys, "argv", ["build_demo_bank.py", "--corpus", str(corpus), "--out", str(out)]
    )
    TOOL.main()


def test_builds_current_bank(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "shard_h_s2000_seed1")
    _write_shard(corpus, "shard_h_s2001_seed2")
    out = tmp_path / "demo_bank.npz"
    _run(monkeypatch, corpus, out)

    with np.load(out) as z:
        assert sorted(z.files) == ["actions", "extra", "masks", "obs", "scalars"]
        assert z["obs"].shape == (6, OBS_CHANNELS, BOARD, BOARD)
        assert z["scalars"].shape == (6, 46)
        assert z["extra"].shape == (6, 57)
        assert z["actions"].shape == (6, 2)
        assert z["masks"].shape == (6, 7)


def test_rejects_v4_extra_dim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "shard_h_s2000_seed1", extra=9)
    with pytest.raises(AssertionError, match="extra"):
        _run(monkeypatch, corpus, tmp_path / "demo_bank.npz")


def test_rejects_v3_scalars_dim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "shard_h_s2000_seed1", scalars=30)
    with pytest.raises(AssertionError, match="scalar"):
        _run(monkeypatch, corpus, tmp_path / "demo_bank.npz")


def test_rejects_old_manifest_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "shard_h_s2000_seed1", obs_schema_major=4)
    with pytest.raises(AssertionError, match="obsSchemaMajor"):
        _run(monkeypatch, corpus, tmp_path / "demo_bank.npz")


# ---- 防守块（2026-11-21）------------------------------------------------------
# `--defense-frames` 用 obs 的既有列做筛选：`scalars[:,38]`=基地压力、`scalars[:,17]`=到基地
# 距离/FIELD_DIAG。这两列的列序来自 `src/nn/obs-encoder.ts`，写错会**静默按错列筛**——
# 所以这里要求建出来的 bank 帧数正好等于造出来的候选帧数（不是"大约"）。

CELLS_PER_S17 = BOARD * 1.5


def _write_defense_shard(root: Path, name: str, *, n: int = 10, **kw: Any) -> Path:
    """造一个 v6 shard，并把前 half 帧设成「基地有压力 + 玩家在 6 格内」。"""
    d = _write_shard(root, name, n=n, v6=True, **kw)
    sc = np.load(d / "scalars.npy")
    half = n // 2
    sc[:half, 38] = 0.9  # 压力拉满
    sc[:half, 17] = 6.0 / CELLS_PER_S17  # 到基地 6 格
    sc[half:, 38] = 0.0
    sc[half:, 17] = 20.0 / CELLS_PER_S17  # 20 格远 ⇒ 不进防守块
    np.save(d / "scalars.npy", sc)
    return d


def _run_args(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["build_demo_bank.py", *argv])
    TOOL.main()


def test_defense_block_only_v6(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_defense_shard(corpus, "shard_s01_seed1", stage=0)
    _write_defense_shard(corpus, "shard_s02_seed1", stage=1)
    out = tmp_path / "bank.npz"
    _run_args(
        monkeypatch,
        "--corpus",
        str(corpus),
        "--out",
        str(out),
        "--v6",
        "--skip-general",
        "--defense-frames",
        "999999",  # 取尽候选
    )
    with np.load(out) as z:
        # 每 shard 10 帧只有前 5 帧满足口径 ⇒ 2×5 = 10，且**不含**普通块
        assert z["obs"].shape[0] == 10
        assert z["masks"].shape == (10, 9)  # v6 掩码宽，不是 7
        assert z["actions"].shape == (10, 2)


def test_defense_block_excludes_dead_games(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """默认只从 stage_clear 局取 ⇒ base_destroyed 局的帧一帧都不进。"""
    corpus = tmp_path / "corpus"
    _write_defense_shard(corpus, "shard_s01_seed1", stage=0, outcome="stage_clear")
    _write_defense_shard(corpus, "shard_s02_seed1", stage=1, outcome="base_destroyed")
    out = tmp_path / "bank.npz"
    _run_args(
        monkeypatch,
        "--corpus",
        str(corpus),
        "--out",
        str(out),
        "--v6",
        "--skip-general",
        "--defense-frames",
        "999999",
    )
    with np.load(out) as z:
        assert z["obs"].shape[0] == 5  # 只剩 stage_clear 那一半


def test_defense_block_requires_v6(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """legacy 语料的 scalars 列序不同 ⇒ 明确拒绝，不静默按错列筛。"""
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "shard_h_s2000_seed1", n=5)
    with pytest.raises(SystemExit, match="--v6"):
        _run_args(
            monkeypatch,
            "--corpus",
            str(corpus),
            "--out",
            str(tmp_path / "bank.npz"),
            "--defense-frames",
            "2",
        )


def test_defense_block_no_candidates_is_loud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """阈值太严时响亮失败，而不是悄悄产出空 bank。"""
    corpus = tmp_path / "corpus"
    _write_defense_shard(corpus, "shard_s01_seed1", stage=0)
    with pytest.raises(SystemExit, match="无候选"):
        _run_args(
            monkeypatch,
            "--corpus",
            str(corpus),
            "--out",
            str(tmp_path / "bank.npz"),
            "--v6",
            "--skip-general",
            "--defense-frames",
            "3",
            "--defense-p",
            "0.99",
        )

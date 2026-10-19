"""`tools/build_demo_bank.py`：当前 schema 维度从 schema 读，旧目录响亮拒绝。

为什么单独一条：这个脚本已经漂过一次（POLICY_EXTRA 9→37 仍硬编码 9）。
本用例同时钉住三件事：
  · 当前 shard（46 scalars / 57 extra / obsSchemaMajor=6）能建出正确的 bank；
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
    scalars: int = 46,
    extra: int = 57,
    obs_schema_major: int = 6,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    np.save(d / "obs.npy", rng.integers(0, 256, (n, 16, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((n, scalars)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((n, extra)).astype(np.float32))
    np.save(d / "actions.npy", rng.integers(0, 2, (n, 2), dtype=np.uint8))
    np.save(d / "masks.npy", np.ones((n, 7), dtype=np.uint8))
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "schemaMajor": obs_schema_major,
                "obsSchemaMajor": obs_schema_major,
                "shard": name,
                "nSamples": n,
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
        assert z["obs"].shape == (6, 16, 26, 26)
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

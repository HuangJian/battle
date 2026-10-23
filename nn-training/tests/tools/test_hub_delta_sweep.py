"""tests/tools/test_hub_delta_sweep.py —— `tools/hub_delta_sweep.py`（2026-10-23 补面）。

这个工具原来**没有测试**，而 2026-10-23 的现场正是它缺席的代价：hub 上 28 轮全是
`weights.delta`，而「基准缺失」只能报、不能修。现在补两件事的钉子：

  1. **`seed_base`**：把一份整份权重放进 hub 的基准搜索面（**文件**形态，幂等，不覆盖别人的基准）
     —— 目录形态（`blob.init/<sha>/`）**不在**搜索模式里，等于没写；
  2. **`--seed` + `--apply`** 走一遍：基准齐 ⇒ 容器被就地补成整份；原件仍在。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import delta_files
from remote.weights_delta import pack_weights_delta
from tools.hub_delta_sweep import seed_base

COURSE = "y31-null"
RUN = "abc123ab"


def _b64(vals: list[float]) -> str:
    return base64.b64encode(b"".join(struct.pack("<f", float(v)) for v in vals)).decode("ascii")


def _full(tag: float) -> bytes:
    return json.dumps(
        {
            "format": "nn-weights-json",
            "version": 1,
            "params": {
                "w": {"shape": [3], "data": _b64([tag, tag + 1.0, tag + 2.0])},
                "b": {"shape": [2], "data": _b64([0.5, 0.25])},
            },
        },
        sort_keys=True,
    ).encode("utf-8")


def _write(p: Path, raw: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return p


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


def test_seed_base_writes_a_file_named_blob_init(tmp_path: Path) -> None:
    """**文件**形态才是搜索面认的（目录等于没写——今天踩过）。"""
    course_dir = tmp_path / COURSE
    raw = _full(0.0)
    sha = seed_base(course_dir, RUN, _write(tmp_path / "base.json", raw), log=lambda _m: None)
    assert sha == hashlib.sha256(raw).hexdigest()
    seeded = course_dir / "remote-jobs" / RUN / "blob.init"
    assert seeded.is_file(), "必须是文件，不是目录"
    assert seeded.read_bytes() == raw
    # 幂等：同一份再 seed 一次不报错、不改字节
    assert seed_base(course_dir, RUN, tmp_path / "base.json", log=lambda _m: None) == sha


def test_seed_base_does_not_overwrite_a_foreign_base(tmp_path: Path) -> None:
    """**同一个 run** 的 `blob.init` 已被别的字节占着 ⇒ 改写 `init_weights.json`，两个都不覆盖。"""
    course_dir = tmp_path / COURSE
    d = course_dir / "remote-jobs" / RUN
    _write(d / "blob.init", _full(9.0))  # 别人的基准（同一 run、同一名字）
    seed_base(course_dir, RUN, _write(tmp_path / "base.json", _full(0.0)), log=lambda _m: None)
    assert (d / "blob.init").read_bytes() == _full(9.0), "别人的基准不许被覆盖"
    assert (d / "init_weights.json").read_bytes() == _full(0.0)


def test_seed_base_refuses_when_both_names_are_taken(tmp_path: Path) -> None:
    course_dir = tmp_path / COURSE
    d = course_dir / "remote-jobs" / RUN
    _write(d / "blob.init", _full(9.0))
    _write(d / "init_weights.json", _full(8.0))
    with pytest.raises(ProtocolError):
        seed_base(course_dir, RUN, _write(tmp_path / "base.json", _full(0.0)), log=lambda _m: None)


def test_cli_seed_then_apply_lands_the_container(tmp_path: Path) -> None:
    """端到端（CLI）：`--seed RUN=权重 --apply` ⇒ 容器被就地补成整份，原件仍在。"""
    from tools.hub_delta_sweep import main

    root = tmp_path / "traj-root"
    course_dir = root / COURSE
    base = _full(0.0)
    off = course_dir / "remote-jobs" / "offline" / RUN / "it-001"
    raw = pack_weights_delta(base, _full(1.0), base_sha=hashlib.sha256(base).hexdigest())
    (off).mkdir(parents=True)
    (off / delta_files.named_for("weights.json", raw)).write_bytes(raw)
    seed_w = _write(tmp_path / "base.json", base)

    # dry-run：不写，且报告「基准缺失」
    assert main(["--traj-root", str(root), "--course", COURSE]) == 1
    assert not (off / "weights.json").exists()

    # 带 --seed + --apply ⇒ 落成整份
    assert main(["--traj-root", str(root), "--course", COURSE, "--seed", f"{RUN}={seed_w}", "--apply"]) == 0
    whole = (off / "weights.json").read_bytes()
    assert b"nn-weights-json" in whole[:64]
    assert (off / "weights.delta").is_file(), "原件是回溯面，必须保留"

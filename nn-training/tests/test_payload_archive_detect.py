"""payload 容器判别回归（2026-09-21 事故，plan/accident.plan.md §4）。

事故链（盘上实锤）：archive 是**完好**的 tar.xz（3501788 字节、`tarfile` 打开正常、
3476 成员），但 `zipfile.is_zipfile()` 的 EOCD 形似字节启发式**误报为 True** ⇒ 旧判别
把它送进 zip 解压 ⇒ `BadZipFile: Bad offset for central directory` ⇒ worker 的
`except Exception` 分支当瞬态重认领（认领 TTL 300s ≈ 5 分钟一节拍）⇒ 同一份毒包复现
约 40 次、零告警空转 3.5 小时，训练侧只看到 3×1800s 超时。

本文件钉死修复后的契约：

  ① **判别顺序 tar 优先**：即使 `is_zipfile` 误报为真，tar.xz 仍被正确解包（误报不再有杀伤力）；
  ② legacy zip payload 仍能解（tar.xz 化之前的包，向后兼容不能破）；
  ③ 不可读容器 / 空容器 = **内容决定性失败** ⇒ `ProtocolError`（不是 `BadZipFile`）
     —— worker 侧据此走 `report_job_failure`，而不是无限重认领。
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.protocol import (
    ProtocolError,
    pack_payload,
    unpack_payload,
)
from remote.worker import unpack_payload_or_fail


def _write_shard(root: Path, name: str = "rl_s0_seed1") -> Path:
    """造一个最小 shard 目录（manifest.json 是 `unpack_payload` 认 shard 的唯一凭据）。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text('{"stage": 0, "seed": 1}', encoding="utf-8")
    (d / "metrics.npy").write_bytes(b"\x93NUMPY fake")
    return d


def _make_tarxz(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    _write_shard(src)
    out = tmp_path / "payload.tar.xz"
    pack_payload([src / "rl_s0_seed1"], {}, out)
    return out


def test_tarxz_unpacks_when_is_zipfile_misreports(tmp_path, monkeypatch) -> None:
    """① 误报不再有杀伤力：`is_zipfile(tar.xz) == True` 时仍走 tar 分支解开。"""
    payload = _make_tarxz(tmp_path)
    monkeypatch.setattr(zipfile, "is_zipfile", lambda _p: True)  # 复现事故的启发式误报
    dest = tmp_path / "out"
    _m, shard_dirs = unpack_payload(payload, dest)
    assert [Path(d).name for d in shard_dirs] == ["rl_s0_seed1"]
    assert (dest / "rl_s0_seed1" / "manifest.json").exists()
    assert (dest / "rl_s0_seed1" / "metrics.npy").read_bytes() == b"\x93NUMPY fake"


def test_legacy_zip_payload_still_extracts(tmp_path) -> None:
    """② tar.xz 化（2026-09-10）之前的 zip 包必须仍可解。"""
    src = tmp_path / "src"
    _write_shard(src)
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(src / "rl_s0_seed1" / "manifest.json", "rl_s0_seed1/manifest.json")
        z.write(src / "rl_s0_seed1" / "metrics.npy", "rl_s0_seed1/metrics.npy")
    _m, shard_dirs = unpack_payload(payload, tmp_path / "out")
    assert [Path(d).name for d in shard_dirs] == ["rl_s0_seed1"]


def test_unreadable_payload_is_deterministic_failure(tmp_path) -> None:
    """③ 垃圾字节 = 内容决定性失败：`ProtocolError`（旧行为是 BadZipFile → 无限重认领）。"""
    payload = tmp_path / "payload.tar.xz"
    payload.write_bytes(b"\x00\x01\x02 not an archive at all")
    with pytest.raises(ProtocolError):
        unpack_payload(payload, tmp_path / "out")


def test_empty_archive_is_deterministic_failure(tmp_path) -> None:
    """③' 空包（tar 打开成功却零成员）也要响亮，不许静默产出空语料。"""
    payload = tmp_path / "empty.tar.xz"
    with tarfile.open(payload, "w:xz"):
        pass
    with pytest.raises(ProtocolError):
        unpack_payload(payload, tmp_path / "out")


def test_worker_wrapper_maps_archive_errors_to_protocol_error(tmp_path) -> None:
    """worker 侧包装：归档异常转 `ProtocolError`（走确定性上报），正常包照常解。"""
    bad = tmp_path / "bad.tar.xz"
    bad.write_bytes(b"PK\x03\x04 still not a zip")
    with pytest.raises(ProtocolError):
        unpack_payload_or_fail(bad, tmp_path / "out-bad")

    payload = _make_tarxz(tmp_path)
    _m, shard_dirs = unpack_payload_or_fail(payload, tmp_path / "out-ok")
    assert [Path(d).name for d in shard_dirs] == ["rl_s0_seed1"]

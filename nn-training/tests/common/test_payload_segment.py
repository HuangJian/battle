"""`common/payload.py` 的手写 ustar 段（2026-10-27 payload 分块并行）。

钉三件事：
  1. **字节**：`ustar_header` 与 `TarInfo(...).tobuf()`（固定字段口径）逐字节相同；
     `shard_segment` 与 `tarfile` 打出的段逐字节相同 —— 否则串联流的成员会被读端解释错位。
  2. **可解**：N 段各自 lzma 压、按序拼接后，`tarfile` 能完整解回（成员数 + 字节数一致）。
  3. **语义**：`on_member` 的三种结局（替换 / 原样 / 丢弃）、`extra_members` 同名拒收、
     内存 `members` 与读盘 `shard_dir` 两条路产出逐字节相同（dispatch 腿 / local 兜底腿）。
"""

from __future__ import annotations

import io
import json
import lzma
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.payload import (
    DROP_MEMBER,
    TAR_END,
    TAR_MODE,
    TAR_MTIME,
    shard_segment,
    ustar_header,
)


def _ref_header(name: str, size: int) -> bytes:
    ti = tarfile.TarInfo(name)
    ti.size = size
    ti.mtime = TAR_MTIME
    ti.mode = TAR_MODE
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti.tobuf()


@pytest.mark.parametrize(
    "name,size",
    [
        ("rl_s2000_seed123/obs.npy", 1234),
        ("rl_s2034_seed1041438816/scalars.npy", 0),
        ("rl_s2000_seed1/a_hold.npy", 132),
        ("rl_s2011_seed42/metrics.npy", 84184),
        ("rl_s2024_seed9/obs.npy", 1427840),
        ("x" * 99, 7),
        ("a/b.npy", 511),
    ],
)
def test_ustar_header_matches_tarinfo(name: str, size: int) -> None:
    assert ustar_header(name, size) == _ref_header(name, size)


def test_ustar_header_rejects_long_name() -> None:
    with pytest.raises(ValueError, match="100 字节"):
        ustar_header("y" * 101, 1)


def _ref_segment(prefix: str, members: dict[str, bytes]) -> bytes:
    """`tarfile` 打出的等价段（去掉结尾 1024B 与 10240 对齐 padding）。"""
    buf = io.BytesIO()
    expect = 0
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for fname in sorted(members):
            data = members[fname]
            ti = tarfile.TarInfo(f"{prefix}/{fname}")
            ti.size = len(data)
            ti.mtime = TAR_MTIME
            ti.mode = TAR_MODE
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
            expect += 512 + ((len(data) + 511) // 512) * 512
    return buf.getvalue()[:expect]


def test_shard_segment_matches_tarfile() -> None:
    members = {"obs.npy": bytes(range(256)) * 40, "scalars.npy": b"\x01" * 184, "empty.npy": b""}
    assert shard_segment("rl_s2000_seed1", members) == _ref_segment("rl_s2000_seed1", members)


def test_memory_and_disk_paths_are_identical(tmp_path: Path) -> None:
    """dispatch 腿（内存 dict）与 local 兜底腿（读盘）必须产出同一份字节。"""
    d = tmp_path / "rl_s2000_seed7"
    d.mkdir()
    files = {"obs.npy": b"\x00" * 1081, "manifest.json": json.dumps({"stage": 2000}).encode()}
    for name, data in files.items():
        (d / name).write_bytes(data)
    a = shard_segment("rl_s2000_seed7", files)
    b = shard_segment("rl_s2000_seed7", shard_dir=d)
    assert a == b


def test_on_member_three_outcomes() -> None:
    members = {"keep.npy": b"K" * 8, "drop.npy": b"D" * 8, "swap.npy": b"S" * 8}

    def on_member(_name: str, fname: str, data: bytes):
        if fname == "drop.npy":
            return DROP_MEMBER
        if fname == "swap.npy":
            return b"X" * 4
        return None

    seg = shard_segment("rl_s1", members, on_member=on_member, extra_members={"reward.npy": b"R" * 6})
    names = _names_of(seg)
    # 顺序 = 盘上成员（按名排序，drop 被丢）在前，`extra_members` 在后（与 `pack_payload`
    # 里 `shard_extra` 追加在盘上成员之后的语义一致）。
    assert names == ["rl_s1/keep.npy", "rl_s1/swap.npy", "rl_s1/reward.npy"], names
    assert b"D" * 8 not in seg


def test_extra_member_name_collision_is_rejected() -> None:
    with pytest.raises(Exception, match="同名"):
        shard_segment("rl_s1", {"reward.npy": b"R"}, extra_members={"reward.npy": b"R2"})


def _names_of(segment_bytes: bytes) -> list[str]:
    """从「无结尾标记的段」里列出成员名（补上 TAR_END 让 tarfile 能读完）。"""
    with tarfile.open(fileobj=io.BytesIO(segment_bytes + TAR_END), mode="r:") as tf:
        return [m.name for m in tf if m.isfile()]


def test_concatenated_segments_decompress_complete(tmp_path: Path) -> None:
    """N 段各自 lzma 压、按序拼接 → `tarfile` 完整解回（成员数 + 每成员字节一致）。"""
    segs, expect = [], {}
    for i in range(5):
        name = f"rl_s2000_seed{i}"
        members = {f"f{j}.npy": bytes([i * 10 + j]) * (100 + 37 * j) for j in range(4)}
        expect.update({f"{name}/{k}": v for k, v in members.items()})
        segs.append(shard_segment(name, members))
    blob = b"".join(lzma.compress(s, preset=1) for s in segs) + lzma.compress(TAR_END, preset=1)
    out = tmp_path / "p.tar.xz"
    out.write_bytes(blob)

    with tarfile.open(out, "r:xz") as tf:
        got = {m.name: tf.extractfile(m).read() for m in tf if m.isfile()}  # type: ignore[union-attr]
    assert got == expect

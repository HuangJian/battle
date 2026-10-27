"""`pack_payload_zip` 的分块并行路径（2026-10-27）。

为什么在 `tests/remote/`：分块调度（lzma + 线程池）住在 `remote/payload_pack.py` ——
`common/payload.py` 的 import 面是闭集（`tests/common/test_payload_split.py` 钉着），
不许引这两个。
"""

from __future__ import annotations

import json
import lzma
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.payload import shard_segment
from remote.payload_pack import pack_payload_zip


def _make_shards(root: Path, n: int = 4) -> list[Path]:
    out = []
    for i in range(n):
        d = root / f"rl_s20{i:02d}_seed{i}"
        d.mkdir(parents=True)
        (d / "obs.npy").write_bytes(bytes([i]) * (300 + i))
        (d / "scalars.npy").write_bytes(bytes([i + 1]) * 184)
        (d / "manifest.json").write_text(json.dumps({"stage": 2000 + i, "seed": i}), "utf-8")
        out.append(d)
    return out


def _members(p: Path) -> dict[str, bytes]:
    with tarfile.open(p, "r:xz") as tf:
        return {m.name: tf.extractfile(m).read() for m in tf if m.isfile()}  # type: ignore[union-attr]


def test_chunks_path_equals_legacy(tmp_path: Path) -> None:
    """`chunks=8`（分块并行）与 `chunks=1`（旧单流）解出的**成员集与字节必须相同**。"""
    shards = _make_shards(tmp_path / "dist")
    extra = tmp_path / "init_weights.json"
    extra.write_bytes(b'{"w": 1}')

    legacy = tmp_path / "legacy.tar.xz"
    fast = tmp_path / "fast.tar.xz"
    pack_payload_zip(shards, [extra], legacy, {}, chunks=1)
    pack_payload_zip(shards, [extra], fast, {}, chunks=8)

    a, b = _members(legacy), _members(fast)
    assert set(a) == set(b), (sorted(a), sorted(b))
    assert a == b, "两块路径的成员字节必须一致"
    # 根级 extra_files 必须**逐字节**进归档根（此前是 `in b` 成员判据，形态门禁把它读成
    # 文本断言 —— 改成取成员比字节：判据没弱，还多钉了「原样搬运」）。
    assert b.get("init_weights.json") == extra.read_bytes(), "根级 extra_files 必须原样进归档根"


def test_presegmented_is_used_verbatim(tmp_path: Path) -> None:
    """`presegmented` 的段被原样采用（dispatch 腿零落盘）：盘上内容变了也不影响结果。"""
    shards = _make_shards(tmp_path / "dist", n=2)
    pre = {d.name: lzma.compress(shard_segment(d.name, shard_dir=d), preset=1) for d in shards}
    expect = {
        f"{d.name}/{f.name}": f.read_bytes() for d in shards for f in d.iterdir() if f.is_file()
    }
    # 打包前把盘上内容改掉：若实现仍读盘，字节就会与 `expect` 不符
    for d in shards:
        (d / "obs.npy").write_bytes(b"\xff" * 999)

    out = tmp_path / "pre.tar.xz"
    pack_payload_zip(shards, [], out, {}, chunks=8, presegmented=pre)
    assert _members(out) == expect, "预分段必须原样采用（零落盘）"

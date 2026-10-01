"""拆分的**契约守卫**：语料归档面永住 `common/payload.py`（S5 第八刀，2026-09-27）。

`common/protocol.py` **1390 → 1243 行**；搬走**三段跨度共 10 名（逐字节不动）**：
`PAYLOAD_NAME` / `PAYLOAD_LEGACY_NAMES` · `INIT_WEIGHTS_NAME` / `PAYLOAD_PERTURB_NAME` /
`PAYLOAD_XZ_PRESET` · `find_payload` / `_add_bytes` / `_extract_archive` / `pack_payload` / `unpack_payload`。

本文件钉五件事：

1. **定义唯一**——这 10 名不许在 `protocol.py` 里再实现一遍；
2. **依赖面闭集（且无环）**——只准 stdlib（`hashlib`/`io`/`json`/`tarfile`/`zipfile`/`pathlib`/
   `typing`/`collections.abc`）+ `common.errors`；**不** import `common.protocol`；
3. **不得碰上层包**（`common/` 是 L0，要随 code.zip 解到云机上）；
4. **转发同一对象**——`protocol` 的每个搬走名都是新模块的转发（`is`）；
5. **归档语义没变**（功能性）：tar.xz 往返 · legacy zip **双读** · 空包**响亮** · 显式扰动换字节 ·
   `find_payload` 优先新名回退旧名。
"""

from __future__ import annotations

import ast
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.payload as payload_mod
import common.protocol as protocol_mod
from common.errors import ProtocolError

PROTO_FILE = ROOT / "common" / "protocol.py"
PAYLOAD_FILE = ROOT / "common" / "payload.py"

MOVED_NAMES = {
    "INIT_WEIGHTS_NAME",
    "PAYLOAD_LEGACY_NAMES",
    "PAYLOAD_NAME",
    "PAYLOAD_PERTURB_NAME",
    "PAYLOAD_XZ_PRESET",
    "_add_bytes",
    "_extract_archive",
    "find_payload",
    "pack_payload",
    "unpack_payload",
}

ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "hashlib",
    "io",
    "json",
    "pathlib",
    "tarfile",
    "typing",
    "zipfile",
    "common.errors",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _imported_modules(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_moved_names_are_defined_in_payload_and_not_in_protocol() -> None:
    """定义唯一：搬走的名字只在新家实现。"""
    assert _defined(PAYLOAD_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(PAYLOAD_FILE))
    leftovers = MOVED_NAMES & _defined(PROTO_FILE)
    assert leftovers == set(), f"protocol.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_payload_import_surface_is_closed_and_acyclic() -> None:
    """★ 依赖面闭集：stdlib + `common.errors`；**不得** import `common.protocol`（成环）。"""
    mods = _imported_modules(PAYLOAD_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"common/payload.py 引入了允许面之外的依赖：{extra}"
    assert "common.protocol" not in mods, (
        "payload 反向 import 了门面 ⇒ 与 protocol 的顶层 import 互引成环"
    )


def test_payload_does_not_touch_an_upper_layer() -> None:
    """`common/` 是 L0：不得 import 上层包。"""
    banned = {"torch", "numpy", "trainer", "worker", "biz", "remote", "models", "data", "train", "common.distribution"}
    tops = {m.split(".")[0] for m in _imported_modules(PAYLOAD_FILE)}
    hit = sorted(tops & banned)
    assert hit == [], f"payload 依赖了上层：{hit}"


def test_protocol_forwards_every_moved_name() -> None:
    """门面：`protocol` 的每个搬走名都还在，且与新家是同一个对象。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(protocol_mod, name), f"common.protocol 丢了转发名 {name}"
        assert getattr(protocol_mod, name) is getattr(payload_mod, name), (
            f"common.protocol.{name} 不是 common.payload.{name}（转发成了副本）"
        )


# ─────────────────────── ⑤ 归档语义没变（功能性） ───────────────────────


def _make_shard(root: Path, stage: int = 1, seed: int = 0) -> Path:
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True)
    (d / "episodes.npy").write_bytes(b"\x00\x01\x02")
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    return d


def test_pack_then_unpack_roundtrip_tar_xz(tmp_path: Path) -> None:
    """tar.xz 往返：打包返回字节 sha；解包给出 shard 目录（含 manifest.json）。"""
    shard = _make_shard(tmp_path / "src")
    out = tmp_path / "job" / payload_mod.PAYLOAD_NAME
    sha = payload_mod.pack_payload([shard], {}, out)
    assert re.fullmatch(r"[0-9a-f]{64}", sha), sha
    assert out.exists()

    manifest, shards = payload_mod.unpack_payload(out, tmp_path / "dest")
    assert manifest == {}  # 新 hub 的 payload 不含根级 manifest.json
    assert [Path(s).name for s in shards] == [shard.name]


def test_unpack_reads_legacy_zip_too(tmp_path: Path) -> None:
    """★ **双读**：legacy `payload.zip` 仍能解（tar.xz 化之前的包不能作废）。"""
    shard = _make_shard(tmp_path / "src")
    zpath = tmp_path / "job" / "payload.zip"
    zpath.parent.mkdir(parents=True)
    with zipfile.ZipFile(zpath, "w") as z:
        for f in sorted(shard.iterdir()):
            z.write(f, arcname=f"{shard.name}/{f.name}")

    manifest, shards = payload_mod.unpack_payload(zpath, tmp_path / "dest")
    assert manifest == {}
    assert [Path(s).name for s in shards] == [shard.name]


def test_extract_prefers_tar_over_the_zip_heuristic(tmp_path: Path) -> None:
    """★ 判别顺序 = **先 tar 后 zip**（2026-09-21 事故：EOCD 启发式把完好 tar.xz 误判成 zip）。"""
    shard = _make_shard(tmp_path / "src")
    out = tmp_path / "job" / payload_mod.PAYLOAD_NAME
    payload_mod.pack_payload([shard], {}, out)
    # 真 tar.xz 必须走 tar 分支（zip 的 local header 过不了 tar 头验证）
    assert tarfile.is_tarfile(out)
    dest = tmp_path / "dest"
    payload_mod._extract_archive(out, dest)
    assert (dest / shard.name / "episodes.npy").read_bytes() == b"\x00\x01\x02"


def test_empty_archive_is_rejected_loudly(tmp_path: Path) -> None:
    """★ 空包必须**响亮**（判别反转后的新失败形态）——不许下游按「零 shard」静默继续。"""
    empty = tmp_path / "job" / payload_mod.PAYLOAD_NAME
    empty.parent.mkdir(parents=True)
    with tarfile.open(empty, "w:xz"):
        pass
    with pytest.raises(ProtocolError, match="解包产物为空"):
        payload_mod.unpack_payload(empty, tmp_path / "dest")


def test_garbage_container_is_a_content_deterministic_error(tmp_path: Path) -> None:
    """既不是 tar 也不是 zip ⇒ `ProtocolError`（内容决定性失败，重领不会自愈）。"""
    bad = tmp_path / "job" / payload_mod.PAYLOAD_NAME
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not an archive at all")
    with pytest.raises(ProtocolError, match="容器双读失败"):
        payload_mod.unpack_payload(bad, tmp_path / "dest")


def test_perturb_changes_the_bytes_without_breaking_the_read(tmp_path: Path) -> None:
    """显式扰动是确定性打包下唯一的换字节手段；读侧忽略根级未知文件（向前兼容）。"""
    shard = _make_shard(tmp_path / "src")
    plain = tmp_path / "a" / payload_mod.PAYLOAD_NAME
    perturbed = tmp_path / "b" / payload_mod.PAYLOAD_NAME
    sha1 = payload_mod.pack_payload([shard], {}, plain)
    sha2 = payload_mod.pack_payload([shard], {}, perturbed, perturb=b"re-roll-1")
    assert sha1 != sha2, "扰动必须换字节（否则重打闭环不收敛）"
    _, shards = payload_mod.unpack_payload(perturbed, tmp_path / "dest")
    assert [Path(s).name for s in shards] == [shard.name]


def test_find_payload_prefers_new_name_then_falls_back(tmp_path: Path) -> None:
    """`find_payload`：优先新名 tar.xz，回退旧名 zip；都没有 ⇒ None。"""
    job = tmp_path / "job"
    job.mkdir()
    assert payload_mod.find_payload(job) is None
    legacy = job / "payload.zip"
    legacy.write_bytes(b"")
    assert payload_mod.find_payload(job) == legacy
    new = job / payload_mod.PAYLOAD_NAME
    new.write_bytes(b"")
    assert payload_mod.find_payload(job) == new

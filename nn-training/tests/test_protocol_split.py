"""拆分的**契约守卫**：`common/protocol.py` 的失败类型族与线格式永住新家（S5 第六刀，2026-09-27）。

`common/protocol.py` **1708 → 1485 行**；两簇搬走（**三段跨度，逐字节不动**）：

* **失败类型族** → `common/errors.py`（117 行）：`ProtocolError` · `RetryableError` ·
  `UnreapableChildError` · `JobCancelledError` · `JobFailedError` · `CodeChangedError`；
* **传输编码 / 线格式** → `common/wire_codec.py`（232 行）：v1 gzip+base64 编解码
  （`_pack_wire`/`_unpack_wire`/`encode_*`/`decode_*` + `_GZIP_MAGIC`/`_WIRE_GZIP_LEVEL`）与
  v2 裸二进制（`pack_job_v2`/`unpack_job_v2`/`pack_result_v2`/`unpack_result_v2` + `WIRE_*`）。

本文件钉六件事：

1. **定义唯一**——这些名字不许在 `protocol.py` 里再实现一遍；
2. **`errors` 是叶子**——它必须**零 import**（除了 `__future__`）：正因为它零依赖，
   `wire_codec` 与 `protocol` 才能都向下依赖它而不成环；
3. **无环**——`wire_codec` 不得 import `common.protocol`（否则与门面互引成环）；
4. **转发同一对象**——`protocol` 的每个搬走名都是新模块的转发（`is`），特别是 `ProtocolError`：
   旧家 `raise` 的与新家的必须是**同一个类**，否则全仓 `except ProtocolError` 会漏接；
5. **依赖面闭集**——两个新模块只准 stdlib（+ `wire_codec` 到 `common.errors`）；
6. **编解码语义没变**（功能性）：v1 往返 + 旧格式（未压缩 base64）自动兼容 + v2 job/result 往返。
"""

from __future__ import annotations

import ast
import base64
import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.errors as errors_mod
import common.protocol as protocol_mod
import common.wire_codec as wire_mod

PROTO_FILE = ROOT / "common" / "protocol.py"
ERRORS_FILE = ROOT / "common" / "errors.py"
WIRE_FILE = ROOT / "common" / "wire_codec.py"

#: 失败类型族（搬走）——只许在 `errors.py` 里定义。
ERROR_NAMES = {
    "CodeChangedError",
    "JobCancelledError",
    "JobFailedError",
    "ProtocolError",
    "RetryableError",
    "UnreapableChildError",
}

#: 线格式/编解码（搬走）——只许在 `wire_codec.py` 里定义。
WIRE_NAMES = {
    "BLOB_FIELDS",
    "WIRE_JOB_CONTENT_TYPE",
    "WIRE_JOB_MAGIC",
    "WIRE_V2_CONTENT_TYPE",
    "WIRE_V2_MAGIC",
    "_GZIP_MAGIC",
    "_HDR_LEN_BYTES",
    "_WIRE_GZIP_LEVEL",
    "_pack_wire",
    "_unpack_wire",
    "decode_opt_tar",
    "decode_weights_json",
    "encode_opt_tar",
    "encode_weights_json",
    "pack_job_v2",
    "pack_result_v2",
    "unpack_job_v2",
    "unpack_result_v2",
}

ALL_MOVED = ERROR_NAMES | WIRE_NAMES


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


def test_moved_names_are_defined_in_new_modules_and_not_in_protocol() -> None:
    """定义唯一：搬走的名字只在各自新家实现。"""
    assert _defined(ERRORS_FILE) >= ERROR_NAMES, sorted(ERROR_NAMES - _defined(ERRORS_FILE))
    assert _defined(WIRE_FILE) >= WIRE_NAMES, sorted(WIRE_NAMES - _defined(WIRE_FILE))
    leftovers = ALL_MOVED & _defined(PROTO_FILE)
    assert leftovers == set(), f"protocol.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_errors_is_a_leaf_module() -> None:
    """★ `common/errors.py` 必须**零 import**——它是叶子，两边都向下依赖它才不会成环。"""
    mods = _imported_modules(ERRORS_FILE) - {"__future__"}
    assert mods == set(), f"common/errors.py 引入了依赖（它必须是无依赖叶子）：{sorted(mods)}"


def test_wire_codec_only_depends_on_stdlib_and_errors() -> None:
    """依赖面闭集：stdlib + `common.errors`。**不得** import `common.protocol`（成环）。"""
    mods = _imported_modules(WIRE_FILE)
    allowed = {
        "__future__",
        "base64",
        "gzip",
        "json",
        "struct",
        "common.errors",
    }
    extra = sorted(mods - allowed)
    assert extra == [], f"common/wire_codec.py 引入了允许面之外的依赖：{extra}"
    assert "common.protocol" not in mods, (
        "wire_codec 反向 import 了门面 ⇒ 与 protocol 的顶层 import 互引成环"
    )


def test_neither_new_module_touches_an_upper_layer() -> None:
    """`common/` 是 L0：两个新家都不得 import 上层包（要随 code.zip 解到云机上）。"""
    banned = {"torch", "numpy", "trainer", "biz", "remote", "models", "data", "train", "common.distribution"}
    for path in (ERRORS_FILE, WIRE_FILE):
        tops = {m.split(".")[0] for m in _imported_modules(path)}
        hit = sorted(tops & banned)
        assert hit == [], f"{path.name} 依赖了上层：{hit}"


def test_protocol_forwards_every_moved_name() -> None:
    """门面：`protocol` 的每个搬走名都还在，且与新家是同一个对象。

    `ProtocolError` 尤其重要——旧家与各腿 `raise`/`except` 的必须是**同一个类**，
    否则 `except ProtocolError` 会静默漏接（本仓最贵的坑之一）。
    """
    for name in sorted(ERROR_NAMES):
        assert hasattr(protocol_mod, name), f"common.protocol 丢了转发名 {name}"
        assert getattr(protocol_mod, name) is getattr(errors_mod, name), (
            f"common.protocol.{name} 不是 common.errors.{name}（转发成了副本）"
        )
    for name in sorted(WIRE_NAMES):
        assert hasattr(protocol_mod, name), f"common.protocol 丢了转发名 {name}"
        assert getattr(protocol_mod, name) is getattr(wire_mod, name), (
            f"common.protocol.{name} 不是 common.wire_codec.{name}（转发成了副本）"
        )


# ─────────────────────── ⑤ 编解码语义没变（功能性） ───────────────────────


def test_v1_wire_roundtrip_and_legacy_compat() -> None:
    """v1：gzip+base64 往返；且靠 gzip 魔数自动兼容**未压缩的旧 base64**。"""
    raw = ("权重中文 " * 500).encode()
    enc = wire_mod.encode_weights_json(raw)
    assert enc != base64.b64encode(raw).decode("ascii"), "应当压缩（不是裸 base64）"
    assert wire_mod.decode_weights_json(enc) == raw
    # 旧格式：未压缩的 base64（历史 result.json / 在途 payload）
    legacy = base64.b64encode(raw).decode("ascii")
    assert wire_mod.decode_weights_json(legacy) == raw
    # 常量一致性：压缩级别就在本模块
    assert wire_mod.encode_opt_tar(raw) == wire_mod.encode_weights_json(raw)
    assert gzip.compress(raw, compresslevel=wire_mod._WIRE_GZIP_LEVEL) is not None


def test_v2_result_roundtrip() -> None:
    """v2 result：blob 字段走裸二进制段，还原成 v1 的 base64 字符串形态。"""
    wj, opt = b"\x1f\x8bweights", b"\x1f\x8bopt"
    result = {
        "job_id": "j1",
        "weights_json": base64.b64encode(wj).decode("ascii"),
        "opt_tar_b64": base64.b64encode(opt).decode("ascii"),
    }
    body = wire_mod.pack_result_v2(result)
    assert body.startswith(wire_mod.WIRE_V2_MAGIC)
    assert wire_mod.unpack_result_v2(body) == result


def test_v2_job_roundtrip() -> None:
    """v2 job：payload/code/ts_code/blob 按固定段序走裸二进制，还原成旧 JSON 形状。"""
    manifest = {"kind": "iter", "proto": 1}
    body = wire_mod.pack_job_v2(manifest, b"payload", b"code", {"opt": b"o"}, b"ts")
    assert body.startswith(wire_mod.WIRE_JOB_MAGIC)
    out = wire_mod.unpack_job_v2(body)
    assert out["manifest"] == manifest
    assert base64.b64decode(out["payload_b64"]) == b"payload"
    assert base64.b64decode(out["code_b64"]) == b"code"
    assert base64.b64decode(out["ts_code_b64"]) == b"ts"
    assert base64.b64decode(out["blobs"]["opt"]) == b"o"


def test_v2_rejects_truncated_and_trailing_garbage() -> None:
    """截断与尾部余料都**响亮拒绝**（不静默截断）——这是 v2 解析端的核心纪律。"""
    from common.errors import ProtocolError

    good = wire_mod.pack_result_v2(
        {
            "weights_json": base64.b64encode(b"w").decode("ascii"),
            "opt_tar_b64": base64.b64encode(b"o").decode("ascii"),
        }
    )
    for bad in (good[: len(good) - 1], good + b"x"):
        try:
            wire_mod.unpack_result_v2(bad)
        except ProtocolError:
            pass
        else:  # pragma: no cover - 走到这里说明静默接受了坏体
            raise AssertionError(f"坏体必须抛 ProtocolError: {bad[:8]!r}…")

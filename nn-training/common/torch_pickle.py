"""common/torch_pickle.py —— **不装 torch** 也能读写 `torch.save` 的 zip 容器（Paddle 转换层）。

## 为什么要有它

用户口径（2026-10-27）：**hub 机统一用 torch 格式**，Paddle worker 自己负责转换；
torch 与 Paddle worker 混跑同一门课。于是 Paddle 侧必须能读 `opt.pt` / `model.pt`（torch
pickle），也必须把它们写回去 —— 而 AI Studio 的镜像里**没有 torch**（这正是本线存在的原因）。

`torch.save` 从 1.6 起就是「zip 容器 + 协议 2 pickle + storages 平铺」的固定布局：

    <prefix>/data.pkl            协议 2 pickle：对象图；张量 = _rebuild_tensor_v2(持久 id, …)
    <prefix>/.format_version     b"1"（读侧据此决定可算 storage 偏移）
    <prefix>/.storage_alignment  b"64"
    <prefix>/byteorder           b"little"
    <prefix>/data/<key>          裸字节（按出现顺序编号，读侧顺序推算偏移 ⇒ **必须连续**）
    <prefix>/version             b"3\\n"
    <prefix>/.data/serialization_id  128-bit 十进制随机数（仅去重/追踪，可缺）

`data.pkl` 里只出现三个全局：`collections.OrderedDict`、`torch.<X>Storage`、
`torch._utils._rebuild_tensor_v2` —— 都是 `torch.load(weights_only=True)` 的白名单项。
本模块因此**手写**这三样（读：白名单 Unpickler；写：协议 2 发射器），把张量换成人手一份的
numpy 数组。`torch.load` 的默认 `weights_only=True`（2.7）能直接吃我们的产物 —— 这是产物的
唯一验收口径（`tests/common/test_torch_pickle.py` 用真 torch 往返）。

## 边界

* **只认 torch.save 的 zip 容器**（torch>=1.6）。更老的「裸 pickle + storages」不认 ——
  本仓产物全是 2.7 写的，多一条腿就是多一份没人跑的代码。
* **只认 little-endian**（`byteorder` 成员）；本仓所有机器都是 x86/ARM 小端。
* 张量一律**物化成独立、C 连续的 numpy 数组**（不保留 storage 共享/视图）：两边消费的都是
  state_dict，逐参数独立；保留视图只会把整块 storage 钉在内存里。
* bf16 / 复数 / UntypedStorage+v3 重建**显式拒收**（响亮，不猜）—— 今天的产物里没有，
  猜错 dtype 比报错危险得多（会静默写坏权重）。

## 依赖面

顶层只有 stdlib + `common.errors`（叶子）⇒ **L0**。numpy **惰性**：hub 的启动闭包会把
`remote.opt_standard` 顶层拉入，而 hub 的启动环境没有 numpy（2026-10-14 事故的同款纪律，见
`remote/opt_delta` 头）。住 `common/` 而不是 `remote/`/`worker/` 的原因：两侧都要向下取
（hub 侧落地 `remote.opt_standard` 与 Paddle worker `worker.ppo.checkpoint_paddle`），而
`worker → remote` 是分层硬禁（`tests/test_layering.py`）。
"""

from __future__ import annotations

import collections
import io
import pickle
import secrets
import struct
import zipfile
from typing import Any

from common.errors import ProtocolError

__all__ = ["TORCH_ZIP_MAGIC_VERSION", "dump_torch_pickle", "load_torch_pickle"]

#: 写侧 `version` 成员的内容（torch 2.x 固定）。
TORCH_ZIP_MAGIC_VERSION = "3\n"

#: 新格式（`.format_version >= 1`）才允许读侧按 `storage_alignment` 推理偏移。
_FORMAT_VERSION = b"1"
_STORAGE_ALIGNMENT = b"64"
_BYTEORDER = b"little"

#: zip 内存储记录的前缀（torch 用文件名主干；读取侧不依赖具体值，只要**有**子目录）。
DEFAULT_PREFIX = "archive"

#: torch storage 类名 → numpy dtype。只列今天真会出现的（bf16/复数显式不在表里）。
_STORAGE_DTYPE: dict[str, str] = {
    "FloatStorage": "float32",
    "DoubleStorage": "float64",
    "HalfStorage": "float16",
    "LongStorage": "int64",
    "IntStorage": "int32",
    "ShortStorage": "int16",
    "CharStorage": "int8",
    "ByteStorage": "uint8",
    "BoolStorage": "bool",
}
#: numpy dtype 字符串 → (torch storage 类名, 每个元素字节数)。写侧用。
_DTYPE_STORAGE: dict[str, str] = {v: k for k, v in _STORAGE_DTYPE.items()}


def _np():
    """惰性 numpy（见模块头：hub 的启动闭包没有 numpy）。"""
    import numpy

    return numpy


# --------------------------------------------------------------------------- 读


class _StorageKind:
    """`find_class` 对 `torch.*Storage` 的替身：只记 dtype 名，不 import torch。"""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name


class _Storage:
    """一块 storage 的原始字节视图（little-endian、只读）。"""

    __slots__ = ("array", "kind")

    def __init__(self, kind: str, array: Any):
        self.kind = kind
        self.array = array


class _TensorRef:
    """`_rebuild_tensor_v2` 的替身：延迟到整棵对象图 unpickle 完再物化。"""

    __slots__ = ("offset", "size", "storage", "stride")

    def __init__(self, storage: _Storage, offset: int, size: tuple, stride: tuple):
        self.storage = storage
        self.offset = offset
        self.size = size
        self.stride = stride


def _rebuild_tensor_v2(
    storage: _Storage,
    storage_offset: int,
    size: tuple,
    stride: tuple,
    requires_grad: Any = False,
    backward_hooks: Any = None,
) -> _TensorRef:
    if not isinstance(storage, _Storage):
        raise ProtocolError("torch pickle：_rebuild_tensor_v2 的第一个参数不是 storage")
    return _TensorRef(
        storage=storage,
        offset=int(storage_offset),
        size=tuple(int(s) for s in size),
        stride=tuple(int(s) for s in stride),
    )


class _TorchUnpickler(pickle.Unpickler):
    """白名单 Unpickler：只认 torch state_dict 会出现的三个全局。"""

    def __init__(self, fh: io.BytesIO, zf: zipfile.ZipFile, root: str):
        super().__init__(fh, encoding="utf-8")
        self._zf = zf
        self._root = root
        self._storages: dict[str, _Storage] = {}

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) == ("collections", "OrderedDict"):
            return collections.OrderedDict
        if module in ("torch", "torch.storage") and name in _STORAGE_DTYPE:
            return _StorageKind(name)
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
            return _rebuild_tensor_v2
        raise ProtocolError(
            f"torch pickle：不认识的全局 {module}.{name}"
            "（只支持 weights_only 可读的 state_dict 容器）"
        )

    def persistent_load(self, pid: Any) -> _Storage:
        if not isinstance(pid, tuple) or len(pid) != 5 or pid[0] != "storage":
            raise ProtocolError(f"torch pickle：无法识别的持久 id {pid!r}")
        _, kind, key, location, numel = pid
        if not isinstance(kind, _StorageKind):
            raise ProtocolError(f"torch pickle：storage 类型不可识别 {kind!r}")
        location = str(location)
        if location != "cpu" and not location.startswith("cuda"):
            raise ProtocolError(f"torch pickle：不支持的 storage 位置 {location!r}")
        key = str(key)
        cached = self._storages.get(key)
        if cached is not None:
            return cached
        np = _np()
        dtype = np.dtype(_STORAGE_DTYPE[kind.name]).newbyteorder("<")
        blob = self._zf.read(self._root + "data/" + key)
        want = int(numel) * dtype.itemsize
        if len(blob) != want:
            raise ProtocolError(
                f"torch pickle：storage {key} 字节数 {len(blob)} != {want}"
                f"（{kind.name} × {int(numel)}）"
            )
        got = _Storage(kind.name, np.frombuffer(blob, dtype=dtype))
        self._storages[key] = got
        return got


def _find_root(names: list[str]) -> str:
    """定位 `<prefix>/data.pkl`：与 torch 一样要求**有子目录**（根级 data.pkl 是非法布局）。"""
    hits = [n for n in names if n.endswith("/data.pkl")]
    if not hits:
        if "data.pkl" in names:
            raise ProtocolError(
                "torch pickle：data.pkl 在 zip 根下（torch 要求它位于子目录里）"
            )
        raise ProtocolError("torch pickle：zip 里没有 */data.pkl")
    if len(hits) > 1:
        raise ProtocolError(f"torch pickle：zip 里有多个 data.pkl：{hits}")
    return hits[0][: -len("data.pkl")]


def _materialize(node: Any) -> Any:
    """把 `_TensorRef` 物化成独立 C 连续数组；dict/OrderedDict/list/tuple 递归保留容器类型。"""
    np = _np()
    if isinstance(node, _TensorRef):
        base = node.storage.array[node.offset :]
        itemsize = base.dtype.itemsize
        if node.size and _is_c_contiguous(node.size, node.stride):
            return np.array(base[: _numel(node.size)].reshape(node.size), copy=True)
        view = np.lib.stride_tricks.as_strided(
            base, shape=node.size, strides=tuple(s * itemsize for s in node.stride)
        )
        return np.array(view, copy=True)
    if isinstance(node, collections.OrderedDict):
        return collections.OrderedDict((k, _materialize(v)) for k, v in node.items())
    if isinstance(node, dict):
        return {k: _materialize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_materialize(v) for v in node]
    if isinstance(node, tuple):
        return tuple(_materialize(v) for v in node)
    return node


def _numel(size: tuple[int, ...]) -> int:
    n = 1
    for s in size:
        n *= s
    return n


def _is_c_contiguous(size: tuple[int, ...], stride: tuple[int, ...]) -> bool:
    expect = 1
    for dim, step in zip(reversed(size), reversed(stride), strict=True):
        if dim == 1:
            continue
        if step != expect:
            return False
        expect *= dim
    return True


def load_torch_pickle(data: bytes) -> Any:
    """torch.save 产物的字节 → 对象图（张量 = numpy 数组）。"""
    buf = io.BytesIO(bytes(data))
    if not zipfile.is_zipfile(buf):
        raise ProtocolError("torch pickle：不是 zip 容器（torch<1.6 的旧格式不支持）")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        root = _find_root(names)
        if root + "byteorder" in names:
            order = zf.read(root + "byteorder").strip()
            if order not in (b"little", b""):
                raise ProtocolError(f"torch pickle：只支持 little-endian（实际 {order!r}）")
        if root + ".format_version" in names:
            ver = zf.read(root + ".format_version").strip()
            if not ver.isdigit() or int(ver) < 1:
                raise ProtocolError(f"torch pickle：未知的 format_version {ver!r}")
        unpickler = _TorchUnpickler(io.BytesIO(zf.read(root + "data.pkl")), zf, root)
        return _materialize(unpickler.load())


# --------------------------------------------------------------------------- 写


class _Emitter:
    """协议 2 的**手写**发射器：只覆盖 state_dict 用到的对象图（dict/list/tuple/标量/张量）。

    不借道 `pickle.Pickler` 的 `persistent_id`：那条路要 `__reduce__` 返回一个**真**函数
    才能写出 `torch._utils._rebuild_tensor_v2` 这个全局，而本机没有 torch。手写 opcode 反而
    更短、更可审计（`pickletools.dis` 与 torch 自己的产出逐段同形）。
    """

    def __init__(self, register):
        self.out = bytearray(b"\x80\x02")  # PROTO 2
        self._register = register

    def dumps(self, obj: Any) -> bytes:
        self._save(obj)
        self.out += b"."
        return bytes(self.out)

    # -- 标量/容器 --

    def _save(self, obj: Any) -> None:
        np = _np()
        if obj is None:
            self.out += b"N"
        elif isinstance(obj, bool):
            self.out += b"\x88" if obj else b"\x89"
        elif isinstance(obj, int):
            self._save_int(obj)
        elif isinstance(obj, float):
            self.out += b"G" + struct.pack(">d", obj)
        elif isinstance(obj, str):
            self._save_str(obj)
        elif isinstance(obj, (np.ndarray, np.generic)):
            self._save_tensor(np.asarray(obj))
        elif isinstance(obj, collections.OrderedDict):
            self._global("collections", "OrderedDict")
            self.out += b")R"
            if obj:
                self.out += b"("
                for k, v in obj.items():
                    self._save(k)
                    self._save(v)
                self.out += b"u"
        elif isinstance(obj, dict):
            self.out += b"}"
            if obj:
                self.out += b"("
                for k, v in obj.items():
                    self._save(k)
                    self._save(v)
                self.out += b"u"
        elif isinstance(obj, list):
            self.out += b"]"
            if obj:
                self.out += b"("
                for v in obj:
                    self._save(v)
                self.out += b"e"
        elif isinstance(obj, tuple):
            if len(obj) == 0:
                self.out += b")"
            elif len(obj) == 1:
                self._save(obj[0])
                self.out += b"\x85"  # TUPLE1
            elif len(obj) == 2:
                self._save(obj[0])
                self._save(obj[1])
                self.out += b"\x86"  # TUPLE2
            elif len(obj) == 3:
                for v in obj:
                    self._save(v)
                self.out += b"\x87"  # TUPLE3
            else:
                self.out += b"("
                for v in obj:
                    self._save(v)
                self.out += b"t"
        else:
            raise ProtocolError(f"torch pickle：无法序列化 {type(obj).__name__}")

    def _save_int(self, v: int) -> None:
        if 0 <= v < 0x100:
            self.out += b"K" + bytes([v])
        elif 0 <= v < 0x10000:
            self.out += b"M" + struct.pack("<H", v)
        elif -(2**31) <= v < 2**31:
            self.out += b"J" + struct.pack("<i", v)
        else:
            n = 1
            while not (-(1 << (8 * n - 1)) <= v < (1 << (8 * n - 1))):
                n += 1
            raw = v.to_bytes(n, "little", signed=True)
            self.out += b"\x8a" + bytes([len(raw)]) + raw  # LONG1

    def _save_str(self, s: str) -> None:
        raw = s.encode("utf-8")
        # 协议 2 只有 BINUNICODE（SHORT_BINUNICODE 是协议 4）。
        self.out += b"X" + struct.pack("<I", len(raw)) + raw

    def _global(self, module: str, name: str) -> None:
        self.out += b"c" + module.encode("ascii") + b"\n" + name.encode("ascii") + b"\n"

    def _save_tensor(self, arr: Any) -> None:
        np = _np()
        arr = np.asarray(arr)
        if arr.dtype == np.dtype("bool"):
            kind = "BoolStorage"
            raw = np.ascontiguousarray(arr).view(np.uint8)
        else:
            if arr.dtype.kind not in "fiu":
                raise ProtocolError(f"torch pickle：不支持的张量 dtype {arr.dtype}")
            if arr.dtype.byteorder == ">":
                arr = arr.astype(arr.dtype.newbyteorder("<"))
            if arr.dtype.name not in _DTYPE_STORAGE:
                raise ProtocolError(f"torch pickle：不支持的张量 dtype {arr.dtype}")
            kind = _DTYPE_STORAGE[arr.dtype.name]
            raw = np.ascontiguousarray(arr)
        storage_key, numel = self._register(raw, arr.shape)
        size = tuple(int(s) for s in arr.shape)
        stride = _c_strides(size)

        self._global("torch._utils", "_rebuild_tensor_v2")
        self.out += b"("  # MARK：REDUCE 的 args
        self.out += b"("  # MARK：持久 id 元组
        self._save_str("storage")
        self._global("torch", kind)
        self._save_str(storage_key)
        self._save_str("cpu")
        self._save_int(numel)
        self.out += b"t"  # TUPLE（持久 id）
        self.out += b"Q"  # BINPERSID（大写 Q；小写 q 是 BINPUT）
        self._save_int(0)  # storage_offset
        self._save_size(size)
        self._save_size(stride)
        self.out += b"\x89"  # NEWFALSE（requires_grad）
        self._global("collections", "OrderedDict")
        self.out += b")R"  # 空 backward_hooks
        self.out += b"t"  # TUPLE（args）
        self.out += b"R"  # REDUCE

    def _save_size(self, values: tuple[int, ...]) -> None:
        if len(values) <= 3:
            for v in values:
                self._save_int(v)
            if len(values) == 0:
                self.out += b")"
            elif len(values) == 1:
                self.out += b"\x85"
            elif len(values) == 2:
                self.out += b"\x86"
            else:
                self.out += b"\x87"
            return
        self.out += b"("  # 4 维以上（conv 权重）：MARK + TUPLE
        for v in values:
            self._save_int(v)
        self.out += b"t"


def _c_strides(size: tuple[int, ...]) -> tuple[int, ...]:
    strides: list[int] = []
    step = 1
    for dim in reversed(size):
        strides.append(step)
        step *= dim
    return tuple(reversed(strides))


def _write_member(zf: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    zf.writestr(info, payload)


def dump_torch_pickle(obj: Any, *, prefix: str = DEFAULT_PREFIX) -> bytes:
    """对象图（numpy 数组 = 张量）→ torch.save 兼容的 zip 字节。"""
    if not prefix or "/" in prefix or "\\" in prefix:
        raise ValueError(f"prefix 必须是单段目录名：{prefix!r}")
    storages: list[tuple[str, bytes]] = []

    def register(raw: Any, shape: tuple) -> tuple[str, int]:
        key = str(len(storages))
        storages.append((key, raw.tobytes(order="C")))
        numel = 1
        for s in shape:
            numel *= int(s)
        return key, numel

    pkl = _Emitter(register).dumps(obj)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        _write_member(zf, prefix + "/data.pkl", pkl)
        _write_member(zf, prefix + "/.format_version", _FORMAT_VERSION)
        _write_member(zf, prefix + "/.storage_alignment", _STORAGE_ALIGNMENT)
        _write_member(zf, prefix + "/byteorder", _BYTEORDER)
        for key, payload in storages:
            _write_member(zf, prefix + "/data/" + key, payload)
        _write_member(zf, prefix + "/version", TORCH_ZIP_MAGIC_VERSION.encode("ascii"))
        _write_member(
            zf,
            prefix + "/.data/serialization_id",
            str(secrets.randbits(128)).encode("ascii"),
        )
    return buf.getvalue()

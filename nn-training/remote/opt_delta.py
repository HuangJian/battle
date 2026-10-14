"""remote/opt_delta.py —— opt 回传的 **delta 容器**（R3 / M4，`plan/wire-diet.plan.md` §3 R3 / §12）。

## 布局

```
MAGIC(4) | uint32 BE header_len | header_json(utf-8) | 各项原始字节…
header = {"ofmt": OPT_DELTA_FMT, "base_sha": <64hex 或 "">, "items": [item, …]}
  item = {"path": "state.0.exp_avg", "kind": "delta-f16" | "exact" | "json",
          "dtype": "float16" / …（json 项无）, "shape": […], "nbytes": N, "value": <json 项才有>}
```

语义（两侧同一份契约）：

* `delta-f16`：逐元素 `(new − base)` 存成 f16 —— 量化施加在**增量**上（|Δ| 小），实测绝对
  max 5.43e-5 / 相对 |opt| 4.7e-5（plan §3 R3）；「base 恒为**本轮载入的那一份**」是残差不
  逐轮累积的前提（两档语义与其偏差口径见 plan §12）；
* `exact`：原样搬字节（Adam `step` 等**非连续量**：量化会改语义）；
* `json`：标量/列表（`param_groups` 的 lr/betas…）内联在头里，逐值精确。

★ 形态**自述**在头里（`ofmt`），且 `apply_opt_delta` 只认 `delta-f16`：旧端（只认整份 tar）拿到容器
会先撞魔数/形态位，**不会**静默把 delta 当权重落盘。形态位在 manifest（`opt_fmt`）里由发布端写下
（兼容矩阵 §4：发布端 ≡ 验收端；缺键 = 旧形态）。

## 为什么住 `remote/` 而不是 `common/wire_codec.py`

两条既有的层守卫把它拦在 `common/` 之外（实测：`tests/common/test_protocol_split.py` 只看
**全量** import 节点 ⇒ 连函数内 `import torch` 也算「碰了上层」，`tests/common/test_common_layer.py`
的同条只扫顶层）：`common/` 要随 `code.zip` 解到**没有 torch** 的云机上 ⇒ 它连惰性 torch 都不许有。
两侧的消费者（worker 的产物段 / hub 的 `verify_and_land`）都在 `remote/` ⇒ 这就是它的家。
顶层只 import stdlib + `common.errors`（叶子）⇒ 拓扑秩 **L0**。

torch **惰性**且缺了**响亮拒收**（M4 硬约束②）：拿不到 torch 就没法重建 ⇒ 拒收该形态并回退整份
blob，绝不静默降级。
"""

from __future__ import annotations

import json
import struct
from typing import Any

from common.errors import ProtocolError

__all__ = [
    "OPT_DELTA_FMT",
    "OPT_DELTA_MAGIC",
    "apply_opt_delta",
    "base_sha_of",
    "opt_fmt_of",
    "pack_opt_delta",
]

#: 容器魔数（旧端拿到它先撞这个，不会把 delta 当权重落盘）。
OPT_DELTA_MAGIC = b"BOD1"
#: 形态自述值（manifest 的 `opt_fmt` 用同一个字面量）。
OPT_DELTA_FMT = "delta-f16"
#: 参与 delta 的 dtype（其余一律 `exact`）——刻意只认浮点：整数张量做差再量化会改语义。
_DELTA_DTYPES = frozenset({"float16", "float32", "float64", "bfloat16"})


def opt_fmt_of(manifest: Any) -> str:
    """manifest 的**形态位读法**（发布端写、worker 与 hub 都照它分叉的唯一口径）。

    命中（= `OPT_DELTA_FMT`）⇒ 回传/落地的都是 delta 容器；否则（含缺键、旧值、非映射）= 旧形态
    （整份 tar，逐字节旧行为）。**键名与合法值只有这一处** —— 两侧各自 `== "delta-f16"` 的话，
    改形态时要改三处，漏一处就是「发布端写了、某一侧没读懂」的静默分叉。
    """
    if not isinstance(manifest, dict):
        return ""
    got = str(manifest.get("opt_fmt") or "")
    return got if got == OPT_DELTA_FMT else ""


def _torch():
    """惰性取 torch（顶层零 torch 是本仓硬规）。缺 ⇒ 响亮（M4 硬约束②：不得静默降级）。"""
    try:
        import torch
    except ImportError as e:  # pragma: no cover - 只有环境真缺 torch 才走到
        raise ProtocolError(
            f"opt delta 需要 torch（{e}）——拒收该形态并回退整份 blob，不得静默当权重落盘"
        ) from None
    return torch


def _opt_items(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """（可嵌套的）state_dict 摊平成 `(path, leaf)`；顺序 = 遍历顺序（**两侧同一份规则**）。"""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _opt_items(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += _opt_items(v, f"{prefix}.{i}")
    else:
        out.append((prefix, obj))
    return out


def _raw_bytes(t, tensor: Any) -> bytes:
    """张量 → 连续原始字节（`view(uint8)` 对全部 dtype 都成立，含 bfloat16）。"""
    return bytes(tensor.detach().contiguous().view(t.uint8).numpy().tobytes())


def pack_opt_delta(base: dict, new: dict, *, base_sha: str) -> bytes:
    """worker 侧：`new` 相对 `base` 的 delta 容器（两者都是 `opt.state_dict()` 那一族形状）。

    `base_sha` = **本机实际载入的那份 base opt tar 的 sha256**（不是「hub 那份」的抽象说法）：
    exact 档下它会与 manifest 的 `opt_sha` 相等，hub 侧即可逐字节对账；identity 档下它只是
    「我拿哪份当基准」的记录（hub 无法核对，靠 `manifest.opt_from` 的身份闸，见 plan §12）。

    路径缺口一律 `ProtocolError`：结构性不一致时**拒绝**，而不是猜一个基准。
    """
    t = _torch()
    known: dict[str, Any] = dict(_opt_items(base))
    items: list[dict] = []
    blobs: list[bytes] = []
    for path, leaf in _opt_items(new):
        if path not in known:
            raise ProtocolError(f"delta：基准里没有 {path}（结构不一致——拒发而不是猜）")
        b = known[path]
        leaf_is_t = t.is_tensor(leaf)
        if leaf_is_t != t.is_tensor(b):
            raise ProtocolError(f"delta：{path} 两侧类型不一致（张量 vs 非张量）")
        if not leaf_is_t:
            try:
                json.dumps(leaf)
            except (TypeError, ValueError) as e:
                raise ProtocolError(f"delta：{path} 既不是张量也不是 JSON 值（{e}）") from None
            items.append({"path": path, "kind": "json", "value": leaf})
            continue
        dtype = str(leaf.dtype).replace("torch.", "")
        shape = [int(x) for x in leaf.shape]
        if dtype in _DELTA_DTYPES and tuple(b.shape) == tuple(leaf.shape):
            raw = _raw_bytes(t, (leaf.float() - b.float()).to(t.float16))
            items.append(
                {"path": path, "kind": "delta-f16", "dtype": "float16", "shape": shape,
                 "nbytes": len(raw)}
            )
        else:
            # `step`（与非同形/非浮点张量）：原样搬字节 —— 非连续量量化会改语义。
            raw = _raw_bytes(t, leaf)
            items.append(
                {"path": path, "kind": "exact", "dtype": dtype, "shape": shape,
                 "nbytes": len(raw)}
            )
        blobs.append(raw)
    hdr = json.dumps(
        {"ofmt": OPT_DELTA_FMT, "base_sha": str(base_sha or ""), "items": items},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return OPT_DELTA_MAGIC + struct.pack(">I", len(hdr)) + hdr + b"".join(blobs)


def base_sha_of(raw: bytes) -> str:
    """容器头里的 `base_sha`（**只读头**，不解 delta 体）；读不动 ⇒ 空串。

    hub 侧的**档位读数**（plan §12）：与 manifest 的 `opt_sha` 相等 = exact 档（worker 下的是我们
    发出去的 blob）；不等 = identity 档（worker 用自己那份 f32 副本当基准 —— hub 无权对账，
    只能记一行判别）。
    """
    off = len(OPT_DELTA_MAGIC)
    if not raw.startswith(OPT_DELTA_MAGIC) or len(raw) < off + 4:
        return ""
    (hdr_len,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    try:
        hdr = json.loads(raw[off : off + hdr_len].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    return str(hdr.get("base_sha") or "")


def apply_opt_delta(base: dict, raw: bytes, *, expect_base_sha: str = "") -> dict:
    """hub 侧：delta 容器加回 `base`（本机那份 base opt 的 state_dict）⇒ **新的** state_dict。

    调用方拿到后 `opt.load_state_dict(...)`（形状/键与 `base` 逐字相同）。`expect_base_sha` 非空时
    逐字节对账基准（exact 档用；identity 档留空，理由见 plan §12）。

    响亮拒收（**不**静默降级）：缺 torch · 魔数/头/长度不符 · `ofmt` 不认 · `base_sha` 与期望不符 ·
    项结构（path/shape）与 `base` 对不上 · 基准不是映射 · 尾部多余字节。
    """
    t = _torch()
    if not raw.startswith(OPT_DELTA_MAGIC):
        raise ProtocolError("delta 容器缺 BOD1 魔数")
    off = len(OPT_DELTA_MAGIC)
    if len(raw) < off + 4:
        raise ProtocolError("delta 容器截断：读不到头长度")
    (hdr_len,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    try:
        hdr = json.loads(raw[off : off + hdr_len].decode("utf-8"))
        items = hdr["items"]
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"delta 头解析失败: {e}") from None
    off += hdr_len
    ofmt = str(hdr.get("ofmt") or "")
    if ofmt != OPT_DELTA_FMT:
        raise ProtocolError(f"delta ofmt={ofmt!r} 不认（只认 {OPT_DELTA_FMT!r}）——拒收")
    got_sha = str(hdr.get("base_sha") or "")
    if expect_base_sha and got_sha != str(expect_base_sha):
        raise ProtocolError(
            f"delta 基准不符：容器 {got_sha[:12]}… != 本机 {str(expect_base_sha)[:12]}… —— 拒收"
        )
    declared: dict[str, tuple[str, Any, str, tuple[int, ...]]] = {}
    for it in items:
        path = str(it.get("path") or "")
        kind = str(it.get("kind") or "")
        if not path or path in declared:
            raise ProtocolError(f"delta item 路径缺失或重复：{path!r}")
        if kind == "json":
            declared[path] = (kind, it.get("value"), "", ())
            continue
        if kind not in ("delta-f16", "exact"):
            raise ProtocolError(f"delta item {path} 的 kind={kind!r} 不认")
        n = int(it.get("nbytes") or 0)
        blob = raw[off : off + n]
        if len(blob) != n:
            raise ProtocolError(f"delta 体截断：{path} 期望 {n} 字节，实得 {len(blob)}")
        off += n
        declared[path] = (
            kind,
            blob,
            str(it.get("dtype") or ""),
            tuple(int(x) for x in it.get("shape") or ()),
        )
    if off != len(raw):
        raise ProtocolError(f"delta 尾部有 {len(raw) - off} 字节多余数据")

    def rebuild(node: Any, prefix: str = "") -> Any:
        if isinstance(node, dict):
            return node.__class__(
                (k, rebuild(v, f"{prefix}.{k}" if prefix else str(k))) for k, v in node.items()
            )
        if isinstance(node, (list, tuple)):
            return node.__class__(rebuild(v, f"{prefix}.{i}") for i, v in enumerate(node))
        got = declared.pop(prefix, None)
        if got is None:
            raise ProtocolError(f"delta 缺 {prefix}（结构不一致——拒收而不是猜）")
        kind, payload, dtype, shape = got
        if kind == "json":
            return payload
        try:
            dt = getattr(t, dtype)
        except AttributeError:
            raise ProtocolError(f"delta {prefix} 的 dtype={dtype!r} 不认") from None
        tensor = t.frombuffer(bytearray(payload), dtype=dt).reshape(shape).clone()
        if kind == "exact":
            return tensor
        if tuple(node.shape) != shape:
            raise ProtocolError(f"delta {prefix} 形状不符：容器 {shape} != 本机 {tuple(node.shape)}")
        return node.float() + tensor.float()

    out = rebuild(base)
    if declared:
        raise ProtocolError(f"delta 多出 base 里没有的项：{sorted(declared)[:3]}… —— 拒收")
    if not isinstance(out, dict):
        raise ProtocolError("delta 基准不是映射（state_dict 应为 dict）——拒收")
    return out

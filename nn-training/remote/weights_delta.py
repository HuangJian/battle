"""remote/weights_delta.py —— result **权重的 delta 容器**（R1 / M3，`plan/wire-diet.plan.md` §3 R1）。

## 布局

```
MAGIC(4) | uint32 BE header_len | header_json(utf-8) | int16 小端载荷（逐项拼接）
header = {"wfmt": W_DELTA_FMT, "base_sha": <64hex 或 "">, "scale": <float>,
          "params_at": <新 JSON 里 "params" 的下标>, "top": {新 JSON 的非 params 键，原序},
          "items": [{"name": …, "shape": […], "nbytes": N}, …]}
```

语义（两侧同一份契约）：回传的是 `Δ = new − base` 的 **int16 + 单 scale** 量化（`q = round(Δ/scale)`，
`scale = max|Δ| / 32767`）；hub 侧 `new = base + q·scale`（fp32）重建。基准恒为**本轮下发的 init 权重**
（双方按 `init_weights_fp` 内容寻址同一份）⇒ 残差 = **本轮**量化误差、**不逐轮累积**
（`W_n = B_{n-1} + Q(A_n − B_{n-1})`，plan §3 R1「累积口径」）。

**为什么不吃 torch**：权重的线上字节是 `nn-weights-json`（`worker/data/weights_io.save_weights_json` 写，
TS 运行时也读它）—— 每个参数是 `{"shape": […], "data": "<f32 LE 的 base64>"}` ⇒ 解析/量化/重建全在
`json` + `base64` + `numpy` 里做完，**hub 不需要 torch**（与 R3/M4 的 opt delta 正相反，那边是 tar+张量）。
顶层只 import stdlib + numpy ⇒ 拓扑秩 **L0**。

**重建产物即权威字节**（与 R3 同口径）：hub 落盘的 `out_weights` 是**重建**出来的 JSON，`init_weights_fp`
= `sha256(落盘字节)` 的算法一字不改；它与 worker 本机那份 f32 全量**必然不同 sha** ⇒ 命中判据是 M0 的
身份映射（`manifest.w_from` 一族），不是「sha 相等」（`remote/course_cache`）。

**形态位**：`manifest.w_fmt = "delta-i16"`（发布端写、worker 与 hub 同读，唯一口径 `w_fmt_of()`）；
缺键 = 旧形态（整份 JSON，逐字节旧行为）。容器头里另**自述** `wfmt` ⇒ 旧端拿到容器先撞魔数，不会把
delta 当权重落盘。

## hub 侧的**落地**（`land_weights_delta`，2026-10-14）

它本来住 `remote/hub_client.py`，撞了 1000 行 LOC 预算后下沉到这里而不是新开模块：新模块只要
import 本模块就会变成 L1，而 `hub_client` 已站在 L1 ⇒ 同层边（DAG 要求严格向下）⇒ 只能把
`hub_client` 抬到 L2，连带 9 个上位模块一路 +1。本模块是 **L0 叶子**，形态的所有者一并管自己那份
文件的落盘最省事（同 `opt_delta` 的处置与理由）。
"""

from __future__ import annotations

import base64
import json
import struct
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from common.errors import ProtocolError
from common.hashing import sha256_bytes
from common.wire_codec import container_base_sha, fmt_bit

__all__ = [
    "W_DELTA_FMT",
    "W_DELTA_MAGIC",
    "apply_weights_delta",
    "base_sha_of",
    "f32_b64",
    "is_container",
    "land_weights_delta",
    "pack_weights_delta",
    "w_fmt_of",
]

#: 容器魔数（旧端拿到它先撞这个，不会把 delta 当权重落盘）。
W_DELTA_MAGIC = b"BWD1"
#: 形态自述值（manifest 的 `w_fmt` 用同一个字面量）。
W_DELTA_FMT = "delta-i16"
#: int16 的对称上限（量化分母；`32767` 而不是 `32768`：`−32768` 侧不留不对称的偏置）。
_Q_MAX = 32767


#: manifest 的**形态位读法**（发布端写、worker 与 hub 都照它分叉的唯一口径）：命中（= `W_DELTA_FMT`）
#: ⇒ 回传/落地的都是权重 delta 容器；否则（含缺键、旧值、非映射）= 旧形态（整份 JSON，逐字节旧行为）。
#: 读法本体在 `common.wire_codec.fmt_bit`（与 R3 的 opt delta **共用一处**：两族容器只差一个值，
#: 各写一份 def 就是同一份判据两处实现）；这里只钉「本族用哪个键、哪个值」。
w_fmt_of = partial(fmt_bit, key="w_fmt", want=W_DELTA_FMT)


def _load(blob: bytes, what: str) -> dict:
    """weights_json 字节 → dict（`params` 必须存在且是映射）。解析不动 ⇒ `ProtocolError`。"""
    try:
        obj = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"{what} 不是合法 JSON（{e}）——拒收") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("params"), dict):
        raise ProtocolError(f"{what} 缺 params 映射（不是 nn-weights-json）——拒收")
    return obj


def _f32(entry: Any, where: str) -> np.ndarray:
    """`{"shape": […], "data": "<b64 f32 LE>"}` → f32 数组（长度/形状不符即响亮拒收）。"""
    if not isinstance(entry, dict) or not isinstance(entry.get("shape"), list):
        raise ProtocolError(f"{where} 不是 {{shape, data}} 形状——拒收")
    shape = [int(x) for x in entry["shape"]]
    try:
        raw = base64.b64decode(str(entry.get("data") or "").encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as e:
        raise ProtocolError(f"{where} 的 data 不是合法 base64（{e}）——拒收") from None
    n = 1
    for x in shape:
        n *= x
    if len(raw) != n * 4:
        raise ProtocolError(f"{where} 的 data {len(raw)} 字节 != {n} 个 f32（形状 {shape}）——拒收")
    return np.frombuffer(raw, dtype="<f4").reshape(shape)


def f32_b64(arr: np.ndarray) -> str:
    """f32 小端数组 → base64（**线上形态**的那一步编码；`weights_io` 写端同款）。

    公开而不是私有：工具侧（`tools/spatial-tower-parity.py` 的 golden 生成）要对同一种线上形态
    编码，自己再抄一份就是「同一份实现两份」（`tools/reuse_audit.py` 的逐字节重复组）。
    """
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f4").tobytes()).decode("ascii")


def _quantized(delta: np.ndarray, scale: float) -> bytes:
    """Δ → int16 小端字节（`round` 用的是 numpy 的银行家舍入，两侧同规则、决定性）。"""
    q = np.rint(delta.astype(np.float64) / scale)
    q = np.clip(q, -_Q_MAX, _Q_MAX).astype("<i2")
    return q.tobytes()


def pack_weights_delta(base_json: bytes, new_json: bytes, *, base_sha: str) -> bytes:
    """worker 侧：`new_json` 相对 `base_json` 的 delta 容器（两者都是 weights_json 的原样字节）。

    `base_sha` = **本机实际载入的那份 init 权重的 sha256**（不是「hub 那份」的抽象说法）：exact 档下它
    与 manifest 的 `init_weights_fp` 相等，hub 侧即可逐字节对账；identity 档下（M0 身份命中）它只是
    「我拿哪份当基准」的记录 —— hub 无法核对，靠身份闸 + 结构闸（同 plan §12 的两档口径）。

    结构缺口一律 `ProtocolError`（**拒发**，不猜基准）：params 集合/形状/dtype 任一对不上。
    """
    base, new = _load(base_json, "base 权重"), _load(new_json, "new 权重")
    bp, params_new = base["params"], new["params"]
    missing = sorted(set(bp) - set(params_new))
    if missing:
        raise ProtocolError(
            f"weights delta：新权重缺 {len(missing)} 个参数（{missing[:3]}…）——拒发（结构不一致）"
        )
    items: list[dict] = []
    deltas: list[np.ndarray] = []
    for name, entry in params_new.items():
        if name not in bp:
            raise ProtocolError(f"weights delta：基准里没有 {name}（结构不一致——拒发而不是猜）")
        b, n0 = _f32(bp[name], f"base[{name}]"), _f32(entry, f"new[{name}]")
        if b.shape != n0.shape:
            raise ProtocolError(f"weights delta：{name} 形状 {b.shape} != {n0.shape}——拒发")
        deltas.append(n0 - b)
    maxabs = max((float(np.abs(d).max()) for d in deltas), default=0.0)
    # 全零 Δ（训练没动权重 / 极小模型）⇒ scale 退 1.0、q 全 0：重建**逐值等于** base。
    scale = (maxabs / _Q_MAX) if maxabs > 0 else 1.0
    blobs = [_quantized(d, scale) for d in deltas]
    for name, entry, raw in zip(params_new.keys(), params_new.values(), blobs, strict=True):
        items.append({"name": name, "shape": [int(x) for x in entry["shape"]], "nbytes": len(raw)})
    # 重建时把 `params` 插回**原来的下标**（`save_weights_json` 的键序里 params 之后还可能有
    # extra_meta 的键）⇒ hub 落盘文件与写端同形，只差 params 的数值本身。
    top = {k: v for k, v in new.items() if k != "params"}
    params_at = list(new).index("params")
    hdr = json.dumps(
        {
            "wfmt": W_DELTA_FMT,
            "base_sha": str(base_sha or ""),
            "scale": float(scale),
            "params_at": int(params_at),
            "top": top,
            "items": items,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return W_DELTA_MAGIC + struct.pack(">I", len(hdr)) + hdr + b"".join(blobs)


def is_container(raw: bytes) -> bool:
    """这份回传字节**是不是** delta 容器（只看魔数）。

    验收端用它分派：是容器 ⇒ 重建；不是 ⇒ **按旧形态整份落盘**（响亮记一行）。
    为什么容忍旧形态（而不是「形态位写了就必须是容器」）：发布端与节点代有可能错位
    （`code.zip` 的热替换/离线缓存的既有面），一个旧代节点不该让整条腿的 job 全废；
    而「是不是容器」是**逐字节可判**的，所以降格只发生在「名实不符」这一档，
    真容器坏掉（截断/头坏/结构不符）仍然**拒收**（`apply_weights_delta`）。
    """
    return raw.startswith(W_DELTA_MAGIC)


def _container_shaped(raw: bytes) -> bool:
    """「身子是 delta 容器、魔数却被改坏」的那一档（逐字节判，`apply_weights_delta` 之外用）。

    为什么单独认它：`land_weights_delta` 对「不是容器」是**宽容**的（旧 worker / 冒烟回显的整份
    JSON 照旧落盘），但**改坏了的容器**不能跟着走那条路 —— 它的身子是 int16 delta 载荷，当权重
    落盘就是静默污染整条链（plan §4 兼容矩阵里最坏的那一格）。判据要求三条同时成立：头长度落在
    字节内 + 头能解析成 JSON 且自述 `wfmt` + 各项 `nbytes` 恰好铺满余下字节。真旧形态的
    nn-weights-json 是文本（`{\n  "params"…`），`raw[4:8]` 当大端长度是个天文数字 ⇒ 撞不齐。
    """
    off = len(W_DELTA_MAGIC)
    if len(raw) < off + 4:
        return False
    (hdr_raw,) = struct.unpack(">I", raw[off : off + 4])
    # `struct.unpack` 是 Any（mypy）：显式 int 化后下面的长度比较才是 `bool` 返回。
    hdr_len = int(hdr_raw)
    off += 4
    if hdr_len <= 0 or off + hdr_len > len(raw):
        return False
    try:
        hdr = json.loads(raw[off : off + hdr_len].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(hdr, dict) or str(hdr.get("wfmt") or "") != W_DELTA_FMT:
        return False
    items = hdr.get("items")
    if not isinstance(items, list):
        return False
    off += hdr_len
    total = 0
    for item in items:
        if not isinstance(item, dict):
            return False
        try:
            total += int(item.get("nbytes") or 0)
        except (TypeError, ValueError):
            return False
    return off + total == len(raw)


#: 容器头里的 `base_sha`（**只读头**，不解 delta 体；读不动 ⇒ 空串）：hub 侧的**档位读数** ——
#: 与 manifest 的 `init_weights_fp`（= 本机那份 init 权重的 sha）相等 = exact 档（worker 下的就是
#: 我们发出去的那份）；不等 = identity 档（worker 用自己那份 f32 副本当基准）。读法本体同样在
#: `common.wire_codec`。
base_sha_of = partial(container_base_sha, magic=W_DELTA_MAGIC)


def apply_weights_delta(base_json: bytes, raw: bytes, *, expect_base_sha: str = "") -> bytes:
    """hub 侧：delta 容器加回 `base_json` ⇒ **新的** weights_json 字节（重建产物 = 权威字节）。

    `expect_base_sha` 非空时逐字节对账基准（exact 档用；identity 档留空，理由见 `pack_weights_delta`）。

    响亮拒收（**不**静默降级）：魔数/头/长度不符 · `wfmt` 不认 · `base_sha` 与期望不符 · 项结构
    （name/shape）与 base 对不上 · 载荷截断或尾部余料 · `params_at` 越界。
    """
    if not raw.startswith(W_DELTA_MAGIC):
        raise ProtocolError("weights delta 容器缺 BWD1 魔数")
    off = len(W_DELTA_MAGIC)
    if len(raw) < off + 4:
        raise ProtocolError("weights delta 容器截断：读不到头长度")
    (hdr_len,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    try:
        hdr: dict[str, Any] = json.loads(raw[off : off + hdr_len].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"weights delta 头解析失败（{e}）——拒收") from None
    off += hdr_len
    if str(hdr.get("wfmt") or "") != W_DELTA_FMT:
        raise ProtocolError(f"weights delta 头自述 wfmt={hdr.get('wfmt')!r} != {W_DELTA_FMT!r}")
    got_sha = str(hdr.get("base_sha") or "")
    if expect_base_sha and got_sha != expect_base_sha:
        raise ProtocolError(
            f"weights delta 基准 sha 不符：容器 {got_sha[:12]}… != 期望 {expect_base_sha[:12]}…——拒收"
        )
    try:
        scale = float(hdr["scale"])
        items: list[dict] = list(hdr["items"])
        top: dict = dict(hdr["top"])
        params_at = int(hdr["params_at"])
    except (KeyError, TypeError, ValueError) as e:
        raise ProtocolError(f"weights delta 头字段缺失/类型不符（{e}）——拒收") from None
    if not (0 <= params_at <= len(top)):
        raise ProtocolError(f"weights delta：params_at={params_at} 越界（top 有 {len(top)} 键）")
    if scale <= 0:
        raise ProtocolError(f"weights delta：scale={scale} 非正——拒收")
    base = _load(base_json, "base 权重")
    bp = base["params"]
    rebuilt: dict[str, Any] = {}
    for item in items:
        name = str(item.get("name") or "")
        if name not in bp:
            raise ProtocolError(f"weights delta：基准里没有 {name}——拒收（结构不符）")
        nbytes = int(item.get("nbytes") or 0)
        blob = raw[off : off + nbytes]
        if len(blob) != nbytes:
            raise ProtocolError(f"weights delta 载荷截断：{name} 期望 {nbytes} 字节，实得 {len(blob)}")
        off += nbytes
        b = _f32(bp[name], f"base[{name}]")
        if b.shape != tuple(int(x) for x in item.get("shape") or []):
            raise ProtocolError(f"weights delta：{name} 形状与基准不符——拒收")
        if nbytes != b.size * 2:
            raise ProtocolError(f"weights delta：{name} 载荷 {nbytes} 字节 != {b.size} 个 int16")
        q = np.frombuffer(blob, dtype="<i2").reshape(b.shape)
        # 全程 fp32 运算（两侧同一表达式 ⇒ 决定性）。
        rebuilt[name] = {
            "shape": list(item["shape"]),
            "data": f32_b64(b + q.astype(np.float32) * np.float32(scale)),
        }
    if off != len(raw):
        raise ProtocolError(f"weights delta 载荷尾部有 {len(raw) - off} 字节多余数据")
    if sorted(rebuilt) != sorted(bp):
        raise ProtocolError(
            f"weights delta：项集合与基准不符（容器 {len(rebuilt)} vs 基准 {len(bp)}）——拒收"
        )
    keys = list(top)
    out: dict[str, Any] = {}
    for k in keys[:params_at]:
        out[k] = top[k]
    out["params"] = rebuilt
    for k in keys[params_at:]:
        out[k] = top[k]
    # 与写端同形（`save_weights_json` 的 `json.dump(meta, f, indent=2)`）：只差 params 的数值。
    return json.dumps(out, indent=2).encode("utf-8")


def land_weights_delta(delta_blob: bytes, *, base_path: str | Path, log) -> bytes:
    """hub 侧的权重落地：delta 容器 → **重建** ⇒ 返回新的 weights_json 字节（权威字节）。

    基准 = 本轮发出去的 init 权重（`base_path` = `init_weights_path`，三重校验①已钉住它与 manifest
    的 `init_weights_fp` 同源）。容器里的 `base_sha` 与**本机这条字节**相等 = **exact** 档 ⇒ 交给
    `apply_weights_delta(expect_base_sha=…)` 逐字节对账；不等 = **identity** 档（worker 用自己那份
    f32 副本当基准 ⇒ hub 无权对账，只记一行判别；plan §12）—— 两档都**照常重建**。

    分派：不是容器（旧 worker / 冒烟回显）⇒ **按旧形态整份落盘**并记一行（响亮），但那份字节得
    先自证**不是被改坏的容器**（`_container_shaped`：头自述 delta-i16 却魔数不符 ⇒ 拒收）；是容器 ⇒ 重建。

    响亮拒收（**绝不**把 delta 当权重落盘）：缺 base / 容器坏 / 形态位不认 / 结构不符 —— 全抛。
    本模块是 **L0 叶子**（理由见模块头）⇒ 这里也用 `ProtocolError`，不 import `remote.hub_http`。
    """
    if not is_container(delta_blob):
        if _container_shaped(delta_blob):
            raise ProtocolError(
                "weights delta 魔数不符但头自述 delta-i16（疑似被改坏的容器）——拒收："
                "绝不把 delta 载荷当权重落盘"
            )
        log(
            "WARN: 形态位是 weights delta 但回传的不是 BWD1 容器（旧 worker / 冒烟回显？）"
            "——按旧形态整份落盘"
        )
        # 刻意 **不** 在这里加强校验（比如「必须是 nn-weights-json」）：那是旧形态的既有宽容度，
        # 本 plan 不改它 —— 冒烟回显与 E2E 的假节点都用**占位字节**当权重，加校验会把它们一起打死。
        # 「不静默」由上面那行 WARN 负责；容器那一路（真 delta）照旧逐字节严格。
        # 唯一不放行的是 `_container_shaped`（上面）：身子是 delta 载荷、魔数被改坏 ⇒ 落盘即污染。
        return delta_blob
    # 空路径 ⇒ `Path("")` 会变成**当前目录**（`exists()` 为真、`read_bytes` 才报 PermissionError）
    # ⇒ 判据用 `is_file()`：目录/空路径都归「base 不在」。
    holder = Path(base_path) if str(base_path) else None
    if holder is None or not holder.is_file():
        raise ProtocolError(
            f"权重形态位={W_DELTA_FMT!r} 但 base 不在（{base_path!r}）——拒收；重发旧形态或关掉 --wire-diet"
        )
    base_raw = holder.read_bytes()
    published = sha256_bytes(base_raw)
    reported = base_sha_of(delta_blob)
    log(
        f"weights delta 落地：base_sha={'exact' if reported == published else 'identity'} 档"
        f"（容器 {reported[:12]}… / 本机 {published[:12]}…）"
    )
    return apply_weights_delta(
        base_raw, delta_blob, expect_base_sha=(published if reported == published else "")
    )

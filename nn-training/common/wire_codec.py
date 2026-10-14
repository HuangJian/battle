"""common/wire_codec —— 「job / result / 权重」在线上**长什么样**（S5 第六刀，2026-09-27）。

从 `common/protocol.py` 整块搬出（**逐字节不动**）。这里是**编解码**的唯一实现，两个层次：

* **v1 传输编码（gzip + base64）**：`_pack_wire` / `_unpack_wire` + `encode_weights_json` /
  `decode_weights_json` / `encode_opt_tar` / `decode_opt_tar`。上行只有 ~220 KB/s，两字段都是
  JSON 文本 / torch 张量，gzip level 6 实测省 29.6%（level 9 换不到额外收益）。解包靠 gzip 魔数
  **自动判别**，兼容未压缩的旧格式（历史 result.json 与在途 payload 照常解出）。
* **v2 裸二进制线格式**：`pack_result_v2` / `unpack_result_v2`（`BRV2`）与 `pack_job_v2` /
  `unpack_job_v2`（`BRJ2`）。布局固定 = `MAGIC(5) | uint32 BE header_len | header_json | blob…`；
  省掉 base64 的 33%。解析端把结果**还原成 v1 的字符串形态** ⇒ 下游零改动。
* **delta 容器的共用读法**：`fmt_bit`（manifest 形态位：命中才认新形态）· `container_base_sha`
  （只读头拿基准指纹 ⇒ exact/identity 两档读数）。两个**同族容器**（`BOD1` opt / `BWD1` 权重，
  见 `remote/opt_delta` 与 `remote/weights_delta`）共用同一套框法与读法；形态常量与容器本体
  归各自模块所有。

**为什么单独成家**：这是「字节 ↔ 对象」的**格式**面，与 manifest/角色的**语义**校验（留在
`protocol`）是两件事；读完一个模块就该知道所有的线上字节形态。依赖面 = stdlib（`base64` /
`gzip` / `json` / `struct`）+ `common.errors`（失败类型是叶子）；**不** import `common.protocol`
（无环）。`common/protocol.py` 保留 `X as X` 门面 ⇒ 历史 import 一行不改。
"""

from __future__ import annotations

import base64
import gzip
import json
import struct

from common.errors import ProtocolError

# ---- result 回传字段的传输编码 ----
# 上行只有 220 KB/s（实测），而两字段都是 JSON 文本 / torch 张量，gzip level 6 实测省
# 29.6%（weights.json 1.35×、opt tar 1.45×，CPU 仅 ~0.05 s）。level 9 换不到额外收益。
_GZIP_MAGIC = b"\x1f\x8b"
_WIRE_GZIP_LEVEL = 6

# ---- result 回传体的 v2 线格式（方案B：gzip 裸二进制，省掉 base64 的 33%）----
# 布局:  MAGIC(5) | uint32 BE header_len | header_json | blob0 | blob1 | ...
# header = {"result": <去掉二进制字段的 dict>, "blob_lens": [len0, len1]}
# blob 顺序固定 = BLOB_FIELDS。
# 动机（2026-09-10 实测）：方案A（gzip+base64）线上 1,150,292 B —— base64 白占 33%。
# 改裸二进制后 862,717 B（再省 25%，相对未压缩的 1,634,596 省 47.2%），上行 ~5.2 -> ~3.9 s。
# hub 只做 base64（stdlib），并把结果**还原成方案A 的字符串形态**再落盘
# ⇒ result.json 格式与下游 hub_client.decode_* 零改动。
WIRE_V2_MAGIC = b"BRV2\n"
WIRE_V2_CONTENT_TYPE = "application/x-battle-result-v2"
BLOB_FIELDS: tuple[str, ...] = ("weights_json", "opt_tar_b64")
_HDR_LEN_BYTES = 4


# ---- /job 提交体的 v2 线格式（M2 B5：payload/code/blob 去 base64，省 25%）----
# 布局:  MAGIC(5) | uint32 BE header_len | header_json | payload | [code] | blob0 | ...
# header = {"manifest": m, "has_code": bool, "blob_names": [...], "lens": [payload, code?, *blobs]}
# 动机：push body 原为 JSON + `payload_b64`（base64 白占 33%）。拆成裸二进制后
# 1.6MB → 1.2MB。解析端保留 JSON 退路（旧节点/旧 hub 混跑时降级）。
WIRE_JOB_MAGIC = b"BRJ2\n"
WIRE_JOB_CONTENT_TYPE = "application/x-battle-job-v2"


def pack_job_v2(
    manifest: dict,
    payload: bytes,
    code: bytes | None,
    blobs: dict[str, bytes] | None = None,
    ts_code: bytes | None = None,
) -> bytes:
    """job 提交体 → v2（payload/code/ts_code/blob 走裸二进制段）。blobs 按名字典序。

    段序固定：payload, code?, ts_code?, *blobs。ts_code（M3：节点跑 rollout 需要的
    TS 运行时 zip）**只有 kind=iter 才有**——其余 job 逐字节与以前一致。
    """
    bl = dict(blobs or {})
    names = sorted(bl)
    lens = [len(payload)]
    if code is not None:
        lens.append(len(code))
    if ts_code is not None:
        lens.append(len(ts_code))
    lens.extend(len(bl[n]) for n in names)
    # `has_ts` **只在该段真的存在时才写**：非 iter 的 job 体因此逐字节与以前一致
    # （本仓的「旧轮字节不变」纪律；解包侧 get("has_ts", False) 兼容缺席）。
    head: dict = {
        "manifest": manifest,
        "has_code": code is not None,
        "blob_names": names,
        "lens": lens,
    }
    if ts_code is not None:
        head["has_ts"] = True
    hdr = json.dumps(head, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    parts = [WIRE_JOB_MAGIC, struct.pack(">I", len(hdr)), hdr, payload]
    if code is not None:
        parts.append(code)
    if ts_code is not None:
        parts.append(ts_code)
    parts.extend(bl[n] for n in names)
    return b"".join(parts)


def unpack_job_v2(body: bytes) -> dict:
    """v2 job 体 → 旧 JSON 形状的 dict（payload_b64/code_b64/blobs）——服务端零下游改动。

    任何长度不符/尾部余料都响亮拒绝，不静默截断（同 unpack_result_v2 规矩）。
    """
    if not body.startswith(WIRE_JOB_MAGIC):
        raise ProtocolError("v2 job 体缺 BRJ2 魔数")
    off = len(WIRE_JOB_MAGIC)
    (hdr_len,) = struct.unpack(">I", body[off : off + _HDR_LEN_BYTES])
    off += _HDR_LEN_BYTES
    try:
        hdr = json.loads(body[off : off + hdr_len].decode("utf-8"))
        manifest: dict = hdr["manifest"]
        has_code: bool = bool(hdr["has_code"])
        # has_ts 缺失（旧 hub 产的 v2 体）= 无 ts_code 段（旧行为，additive）。
        has_ts: bool = bool(hdr.get("has_ts", False))
        names: list = hdr["blob_names"]
        lens: list = hdr["lens"]
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"v2 job 头解析失败: {e}") from None
    off += hdr_len
    expected = 1 + (1 if has_code else 0) + (1 if has_ts else 0) + len(names)
    if len(lens) != expected:
        raise ProtocolError(f"v2 job lens 长度 {len(lens)} != {expected}")
    out: dict = {"manifest": manifest}

    def _chunk(n: int, what: str) -> bytes:
        nonlocal off
        blob = body[off : off + n]
        if len(blob) != n:
            raise ProtocolError(f"v2 job 体截断：{what} 期望 {n} 字节，实得 {len(blob)}")
        off += n
        return blob

    out["payload_b64"] = base64.b64encode(_chunk(int(lens[0]), "payload")).decode("ascii")
    i = 1
    if has_code:
        out["code_b64"] = base64.b64encode(_chunk(int(lens[i]), "code")).decode("ascii")
        i += 1
    if has_ts:
        out["ts_code_b64"] = base64.b64encode(_chunk(int(lens[i]), "ts_code")).decode("ascii")
        i += 1
    if names:
        bm: dict = {}
        for name in names:
            bm[str(name)] = base64.b64encode(_chunk(int(lens[i]), f"blob {name}")).decode("ascii")
            i += 1
        out["blobs"] = bm
    if off != len(body):
        raise ProtocolError(f"v2 job 体尾部有 {len(body) - off} 字节多余数据")
    return out


def pack_result_v2(result: dict) -> bytes:
    """result dict（二进制字段为方案A 的 base64 串）-> v2 体。

    只把 BLOB_FIELDS 从 JSON 里搬出来当二进制段，**不重新压缩**（入参已是 gzip 后的
    base64，解开即是 gzip 字节）；JSON 头保留其余全部字段 ⇒ 还原后语义逐字段一致。
    """
    blobs = [base64.b64decode(str(result.get(f, "") or "").encode("ascii")) for f in BLOB_FIELDS]
    head = {k: v for k, v in result.items() if k not in BLOB_FIELDS}
    hdr = json.dumps(
        {"result": head, "blob_lens": [len(b) for b in blobs]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return WIRE_V2_MAGIC + struct.pack(">I", len(hdr)) + hdr + b"".join(blobs)


def unpack_result_v2(body: bytes) -> dict:
    """v2 体 -> result dict（二进制字段还原成方案A 的 base64 串）。hub 侧用。

    只依赖 base64/json/struct，**不需要 gzip**（blob 原样重新 base64 即可）。
    任何长度不符/尾部余料都响亮拒绝，不静默截断。
    """
    if not body.startswith(WIRE_V2_MAGIC):
        raise ProtocolError("v2 体缺 BRV2 魔数")
    off = len(WIRE_V2_MAGIC)
    (hdr_len,) = struct.unpack(">I", body[off : off + _HDR_LEN_BYTES])
    off += _HDR_LEN_BYTES
    try:
        hdr = json.loads(body[off : off + hdr_len].decode("utf-8"))
        head: dict = hdr["result"]
        lens = hdr["blob_lens"]
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"v2 头解析失败: {e}") from None
    off += hdr_len
    if len(lens) != len(BLOB_FIELDS):
        raise ProtocolError(f"v2 blob_lens 长度 {len(lens)} != {len(BLOB_FIELDS)}")
    for field, n in zip(BLOB_FIELDS, lens, strict=True):
        blob = body[off : off + n]
        if len(blob) != n:
            raise ProtocolError(f"v2 体截断：{field} 期望 {n} 字节，实得 {len(blob)}")
        off += n
        head[field] = base64.b64encode(blob).decode("ascii")
    if off != len(body):
        raise ProtocolError(f"v2 体尾部有 {len(body) - off} 字节多余数据")
    return head


# ---- delta 容器（`BOD1` / `BWD1`）的**共用读法**（2026-10-14，plan/wire-diet §12/§13）----
# R3（opt delta）与 R1（权重 delta）是两个**同族容器**：`MAGIC(4) | uint32 BE header_len |
# header_json | blob…`（与上面 `BRV2`/`BRJ2` 同一套框法）。形态位读法与「只读头拿 base_sha」
# 若各写一份，就是同一份判据两处实现 —— 改一处漏一处即静默分叉（worker 发新形态、hub 按旧
# 形态解）。⇒ 读法只此一处；形态常量（`OPT_DELTA_FMT`/`W_DELTA_FMT` 与两个魔数）仍归各自的
# 容器模块所有 —— 谁改形态、谁改一个值，`import` 一眼可见。
def fmt_bit(manifest: object, *, key: str, want: str) -> str:
    """manifest 的**形态位读法**：逐字等于 `want` 才返回值，否则 `""`（= 旧形态）。

    缺键 / 旧值 / 脏值 / 非映射一律回 `""` ⇒ 调用方「命中才走新形态，否则逐字节旧行为」。
    """
    if not isinstance(manifest, dict):
        return ""
    got = str(manifest.get(key) or "")
    return got if got == want else ""


def container_base_sha(raw: bytes, *, magic: bytes) -> str:
    """「`magic` + uint32 BE 头长 + header_json + 载荷」这种容器头里的 `base_sha`（**只读头**）。

    读不动（没魔数 / 截断 / 头不是 JSON 对象）⇒ `""`。hub 侧的**档位读数**：与 manifest 里的
    指纹相等 = exact 档（worker 下的是我们发出去的那份字节）；不等 = identity 档（worker 用
    自己那份权威副本当基准 ⇒ 无权对账，只记一行判别）。
    """
    off = len(magic)
    if not raw.startswith(magic) or len(raw) < off + 4:
        return ""
    (hdr_len,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    try:
        hdr = json.loads(raw[off : off + hdr_len].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(hdr, dict):
        return ""
    return str(hdr.get("base_sha") or "")


def _pack_wire(raw: bytes) -> str:
    """原始字节 → gzip → base64（JSON 安全的回传字段）。"""
    return base64.b64encode(gzip.compress(raw, compresslevel=_WIRE_GZIP_LEVEL)).decode("ascii")


def _unpack_wire(b64: str) -> bytes:
    """base64 → (必要时 gunzip) → 原始字节。

    靠 gzip 魔数自动判别，**兼容旧格式**（未压缩的 base64）—— 历史 result.json 与
    已在途的 payload 都能照常解出。
    """
    raw = base64.b64decode(b64.encode("ascii"))
    if raw[:2] == _GZIP_MAGIC:
        return gzip.decompress(raw)
    return raw


def encode_weights_json(wj: bytes) -> str:
    """weights_json 传输编码（gzip + base64）。"""
    return _pack_wire(wj)


def decode_weights_json(b64: str) -> bytes:
    """weights_json 传输解码（hub 落盘 args.out 前用）；兼容未压缩的旧格式。"""
    return _unpack_wire(b64)


def encode_opt_tar(tar_bytes: bytes) -> str:
    """opt tar 传输编码（gzip + base64）。"""
    return _pack_wire(tar_bytes)


def decode_opt_tar(b64: str) -> bytes:
    """opt tar 传输解码；兼容未压缩的旧格式。"""
    return _unpack_wire(b64)

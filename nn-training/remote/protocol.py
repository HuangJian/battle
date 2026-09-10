"""remote/protocol.py — remote PPO job protocol (pure, torch-free, stdlib-only).

Single source of truth for the wire contract between the hub (TrainingLoop remote
branch + hub-server) and the cloud worker (`python -m remote_worker`). Design doc:
`plan/remote-ppo-architecture.md` §6 (D1/D5/D6/D9/D12), manifest fields §13 附录 A.

Contents (all pure functions / constants — no torch, no ppo import):
  * manifest validation (required-field fail fast, unknown fields ignored)
  * `data_fp` — sha256 over sorted shard relative paths + each manifest's
    {wver, stage, seed} (D1; local recompute == manifest value on both sides)
  * payload zip pack/unpack (shard dirs + manifest.json), with `payload_sha256`
  * idempotency key = (runId, it, init_weights_fp, data_fp) (D1)
  * per-job deterministic numpy seed = hash(runId, it, init_weights_fp) (D5)
  * result envelope validation (weights_json + opt_tar + agg + commit_echo)
  * auth: bearer token header name (D9)

Rationale for torch-free: the hub (TrainingLoop remote branch + hub-server) must
never import torch (D2) — this module is imported by both hub-side and worker-side
code, so it must be importable with zero torch/numpy cost (numpy is acceptable;
test_no_torch_on_import guards the `import run_rl` chain).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import struct
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

# ------------------------------------------------------------------ constants

PROTO = 1  # 协议版本：未知字段忽略，缺失必填 fail fast（D1）
# §343（2026-09-06）：job 分发改为竞速广播（先回传结果者胜，store_result 首写锁定，
# 落后者 409 丢弃），租约/心跳不再参与调度。LEASE_SEC/HEARTBEAT_SEC 仅剩兼容职责：
# 旧租约路径（_JobStore.claim/heartbeat/release + worker 心跳线程守卫）仍在，hub
# 重启即丢租约、D8 账本重建语义不变。
LEASE_SEC = 30 * 60  # （兼容）旧租约时长；竞速模型下无调度职责
HEARTBEAT_SEC = 60  # （兼容）旧心跳周期；仅旧租约模式 hub 的 worker 心跳线程使用
AUTH_HEADER = "Authorization"  # Bearer <token>（D9；token 永不落盘/落日志）

#: manifest 必填字段（附录 A；缺失任一 → 校验失败）
MANIFEST_REQUIRED = (
    "proto",
    "runId",
    "it",
    "job_id",
    "commit",
    "code_sha256",  # Python 源码 zip sha256（hub 启动时打包，替代 git 同步）
    "course",  # 课程 jsonc 全文快照（reward_spec 重建输入，D6）
    "course_fp",  # 课程文件 sha256（语料血缘，D14）
    "reward_formula",
    "formula_hash",
    "metrics_version",
    "gamma",
    "lam",
    "mode",
    "seed",  # per-job numpy 种子 = hash(runId,it,init_weights_fp)（D5）
    "epochs",
    "mb",
    "lr",
    "init_weights_fp",
    "data_fp",
    "payload_sha256",
)
#: 可选字段（缺失给默认；未知字段忽略——proto=1 语义）
MANIFEST_OPTIONAL_DEFAULTS: dict[str, object] = {
    "kl_coef": 0.0,
    "kl_cap": None,  # None = 不覆盖，由 policy.streamKlCap 决定
    "adv_norm": "auto",
    "normalize_ret": False,  # R5：ret 归一；缺失（旧 hub）= 关
    "kickstart_kl": 0.0,  # §363：BC 缰绳系数（已衰减）；0 = 关
    "ref_weights_b64": "",  # §363：BC ref 权重 base64；空 = 无
    "ref_weights_fp": "",  # §363：上者 sha256（有字节时必对上）
    "shuffle": True,
    "schedule_raw": [],  # ppo_schedule 解析前原始表（审计）
    "opt_init": "",  # base64 tar（model/opt/numpy RNG）；空 = 无（首轮）
}


class ProtocolError(ValueError):
    """协议违规（缺失必填 / 类型错 / 哈希不匹配）。调用方（hub/worker）决定拒收方式。"""


class RetryableError(Exception):
    """瞬时失败（网络抖动 / 5xx / 传输损坏）——租约窗口内重试即可修复，非确定性拒绝。

    与 ProtocolError 的分界（2026-09-05，DECISIONS §340 补充 3）：4xx/字段级校验
    失败 = 确定性拒绝（重试无意义）；网络层异常与 5xx = 可重试。worker_loop 捕获
    RetryableError 后主动 release 租约回池，立即可重领（不再干等 30min 过期）。"""


def normalize_manifest(m: dict) -> dict:
    """校验 + 归一化 job manifest（proto=1：缺失必填 fail fast，未知字段忽略）。

    返回浅拷贝的 manifest（必填齐全、可选字段带默认值）。校验失败抛
    `ProtocolError`，错误信息指明缺失字段。
    """
    if not isinstance(m, dict):
        raise ProtocolError(f"manifest 必须是对象，收到 {type(m).__name__}")
    missing = [k for k in MANIFEST_REQUIRED if k not in m]
    if missing:
        raise ProtocolError(f"manifest 缺失必填字段: {missing}")
    if int(m.get("proto", -1)) != PROTO:
        raise ProtocolError(f"proto={m.get('proto')!r} != {PROTO}（协议版本不匹配）")
    out = dict(m)
    for k, v in MANIFEST_OPTIONAL_DEFAULTS.items():
        out.setdefault(k, v)
    # 标量类型校验（fail fast，防拼错/串位）
    if not isinstance(out["runId"], str) or not out["runId"]:
        raise ProtocolError("runId 必须是非空 str")
    for k in ("it", "epochs", "mb", "metrics_version"):
        if not isinstance(out[k], int) or isinstance(out[k], bool):
            raise ProtocolError(f"{k} 必须是 int，收到 {out[k]!r}")
    for k in ("gamma", "lam", "lr"):
        v = out[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ProtocolError(f"{k} 必须是 float，收到 {v!r}")
        if float(v) <= 0:
            raise ProtocolError(f"{k} 必须 > 0，收到 {v!r}")
    for k in (
        "commit",
        "code_sha256",
        "course",
        "course_fp",
        "mode",
        "seed",
        "init_weights_fp",
        "data_fp",
        "payload_sha256",
        "job_id",
    ):
        if not isinstance(out[k], str) or not out[k]:
            raise ProtocolError(f"{k} 必须是非空 str")
    if out["mode"] != "per-tick":
        raise ProtocolError(
            f"mode={out['mode']!r} != 'per-tick'（v1 红线：仅 per-tick 课程支持远程）"
        )
    if not isinstance(out.get("normalize_ret", False), bool):
        raise ProtocolError(f"normalize_ret 必须是 bool，收到 {out.get('normalize_ret')!r}")
    if not isinstance(out.get("kickstart_kl", 0.0), (int, float)) or isinstance(
        out.get("kickstart_kl", 0.0), bool
    ):
        raise ProtocolError(f"kickstart_kl 必须是 number，收到 {out.get('kickstart_kl')!r}")
    if float(out.get("kickstart_kl", 0.0)) < 0:
        raise ProtocolError("kickstart_kl 必须 >= 0")
    return out


# ------------------------------------------------------------------ data_fp


def data_fp(shard_dirs: Sequence[str | Path]) -> str:
    """D1 data_fp：sha256(按字典序排列的 shard 相对路径 + 各 manifest {wver,stage,seed})。

    shard_dirs：本轮应训 shard 目录（绝对/相对路径均可，按 basename 字典序排序——
    排序以 shard 目录名（rl_s{stage}_seed{seed}）为键，与打包/装载口径一致）。
    任一侧重算必须得到同一值（hub 打包时写入、验收时本地重算比对——防 ABA，D12）。
    """
    entries: list[tuple[str, str, int, int]] = []
    for d in shard_dirs:
        p = Path(d)
        mp = p / "manifest.json"
        try:
            with open(mp, encoding="utf-8") as f:
                mm = json.load(f)
        except (OSError, ValueError) as e:
            raise ProtocolError(f"data_fp: 读 {mp} 失败: {e}") from e
        entries.append(
            (
                p.name,  # rl_s{stage}_seed{seed}
                str(mm.get("wver", "")),
                int(mm.get("stage", -1)),
                int(mm.get("seed", -1)),
            )
        )
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    for name, wver, stage, seed in entries:
        h.update(name.encode("utf-8"))
        h.update(wver.encode("utf-8"))
        h.update(str(stage).encode("utf-8"))
        h.update(str(seed).encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------------------ payload
# ---- payload 容器（2026-09-10：zip/deflate -> tar.xz）----
# 实测（20 个真实 c5-margin shard，裸 22.3 MB，×7.5 折算 150 份）：
#   ZIP_DEFLATED(6)  241,382 B / 1.8 s
#   ZIP_LZMA         187,443 B / 9.0 s   （只 −22.3% 且慢 5×，已否决）
#   tar.xz(preset=3) 123,788 B / 1.9 s   <== 采用：体积 −48.7%，打包耗时持平
# 折算真实 payload 3.83 MB -> ~1.96 MB，下载 2.3 s -> ~1.2 s。stdlib，无新依赖。
# 解析端**双读**（zipfile.is_zipfile 判别）⇒ 旧 hub 产的 payload.zip 与新 hub 产的
# payload.tar.xz 对新旧 worker 都能工作。
PAYLOAD_NAME = "payload.tar.xz"
PAYLOAD_LEGACY_NAMES: tuple[str, ...] = ("payload.zip",)
# 标注成 Literal：typeshed 的 tarfile.open("w:xz") 重载要求 preset 为 Literal[0..9]，
# 普通 int 过不了 mypy。**改档位时这里要同步改**（比如变 5 就写 Literal[5]）。
PAYLOAD_XZ_PRESET: Literal[3] = 3


def find_payload(job_dir: str | Path) -> Path | None:
    """定位 job 目录下的 payload（优先新名 tar.xz，回退旧名 zip）——新旧互通。"""
    jd = Path(job_dir)
    for name in (PAYLOAD_NAME, *PAYLOAD_LEGACY_NAMES):
        p = jd / name
        if p.exists():
            return p
    return None


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    """把一个内存字节串写进 tar（避免为 manifest 落临时文件）。"""
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


def _extract_archive(src: Path, dest: Path) -> None:
    """解包 payload 归档：**双读** zip / tar.xz（按内容判别，不看扩展名）。"""
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            z.extractall(dest)
        return
    with tarfile.open(src, "r:*") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 无 filter 参数
            tf.extractall(dest)


def pack_payload(shard_dirs: list[str | Path], manifest: dict, out_path: str | Path) -> str:
    """把 shard 目录（npy + manifest.json）打成 **tar.xz**，写 `out_path`。

    容器演进（2026-09-10）：原为 zip/deflate —— 实测 tar.xz(preset=3) 体积 −48.7%
    而打包耗时持平（stdlib、无新依赖），解析端 `unpack_payload` 双读兼容。
    布局不变：每个 shard 目录整体进入（目录名 rl_s{stage}_seed{seed}/…），根下再写
    一份 manifest.json（payload_sha256 占位空串——最终哈希由调用方对**本函数产出的
    字节**计算后回填 job 记录，worker 以 job 记录的 payload_sha256 对原始下载字节
    校验，D1——防隧道截断）。

    返回文件字节 sha256。调用方拿到后应把 sha 写入 job 记录/账本。
    """
    zpath = Path(out_path)
    zpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = zpath.with_suffix(zpath.suffix + ".tmp")
    with tarfile.open(tmp, "w:xz", preset=PAYLOAD_XZ_PRESET) as tf:
        for d in shard_dirs:
            p = Path(d)
            if not p.is_dir():
                raise ProtocolError(f"pack_payload: shard 目录不存在 {p}")
            for f in sorted(p.iterdir()):
                if f.is_file():
                    tf.add(f, arcname=f"{p.name}/{f.name}")
        _add_bytes(tf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    tmp.replace(zpath)
    return hashlib.sha256(zpath.read_bytes()).hexdigest()


def unpack_payload(payload_path: str | Path, dest: str | Path) -> tuple[dict, list[str]]:
    """解包 payload → (manifest, shard_dir_paths)。**双读**：zip 与 tar.xz 都支持。

    shard_dir_paths 为解包后落在 dest 下的各 shard 目录（含 manifest.json），
    供 worker 的 load_episodes 消费。返回的 manifest 为归档内副本（payload_sha256
    为占位空串）——**不作权威校验**；worker 必须用 job 记录（/jobs/next 返回）
    的 manifest 做 payload_sha256 / commit / mode 等全部校验（本函数只解包）。
    """
    dest_p = Path(dest)
    dest_p.mkdir(parents=True, exist_ok=True)
    _extract_archive(Path(payload_path), dest_p)
    mp = dest_p / "manifest.json"
    with open(mp, encoding="utf-8") as f:
        manifest = json.load(f)
    shard_dirs: list[str] = []
    for p in sorted(dest_p.iterdir()):
        if p.is_dir() and (p / "manifest.json").exists():
            shard_dirs.append(str(p))
    return manifest, shard_dirs


# ------------------------------------------------------------------ idempotency


def idempotency_key(manifest: dict) -> tuple:
    """D1 幂等键 = (runId, it, init_weights_fp, data_fp)。云 worker 崩溃重拉同一
    job 时按此去重；hub 账本记 job 状态，不重复发包已完成 job。"""
    return (
        manifest["runId"],
        manifest["it"],
        manifest["init_weights_fp"],
        manifest["data_fp"],
    )


def job_id(manifest: dict) -> str:
    """job_id 派生：sha256(幂等键)[:16]——同一 job（幂等键相同）永远同一 job_id，
    天然幂等（hub kill -9 重启后重发布不产生重复 job）。"""
    h = hashlib.sha256()
    for part in idempotency_key(manifest):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def job_seed(run_id: str, it: int, init_weights_fp: str) -> str:
    """D5 per-job 确定性种子 = hash(runId, it, init_weights_fp)（十六进制串）。

    云 worker 以它为 numpy 种子重新播种后再 load/chunk/update——同 job 重发
    chunk 逐字节一致；跨进程（本地 vs 云）chunk 顺序差异为预期内（D7）。
    """
    h = hashlib.sha256()
    h.update(run_id.encode("utf-8"))
    h.update(str(it).encode("utf-8"))
    h.update(init_weights_fp.encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------------------ result


def validate_result(
    r: dict,
    manifest: dict,
    *,
    commit_echo_must_match: bool = True,
) -> dict:
    """云回传结果校验（附录 A）：job_id/data_fp/init_weights_fp 与 manifest 对账 +
    weights_json 非空 + agg 关键字段。返回归一化结果。校验失败抛 ProtocolError。

    commit_echo_must_match=False：hub 侧对账时用（hub 不依赖云 echo 决定 commit
    是否一致——manifest.commit 是 hub 自己写的，echo 只是审计）。
    """
    if not isinstance(r, dict):
        raise ProtocolError(f"result 必须是对象，收到 {type(r).__name__}")
    for k in ("job_id", "data_fp", "init_weights_fp", "weights_json", "commit_echo"):
        if k not in r:
            raise ProtocolError(f"result 缺失字段 {k}")
    if r["job_id"] != manifest["job_id"]:
        raise ProtocolError(
            f"result.job_id={r['job_id']!r} != manifest.job_id={manifest['job_id']!r}——job 混用"
        )
    if r["data_fp"] != manifest["data_fp"]:
        raise ProtocolError("result.data_fp != manifest.data_fp——训练语料漂移（拒收）")
    if r["init_weights_fp"] != manifest["init_weights_fp"]:
        raise ProtocolError("result.init_weights_fp != manifest.init_weights_fp（拒收）")
    if commit_echo_must_match and r["commit_echo"] != manifest["commit"]:
        raise ProtocolError("result.commit_echo != manifest.commit——代码版本不一致（拒收）")
    wj = r["weights_json"]
    if not isinstance(wj, (str, bytes)) or len(wj) == 0:
        raise ProtocolError("result.weights_json 必须非空（base64 或原始字节）")
    agg = r.get("agg")
    if not isinstance(agg, dict) or not all(
        k in agg for k in ("policy", "value", "entropy", "kl", "mean_ret")
    ):
        raise ProtocolError(f"result.agg 缺关键字段: {agg!r}")
    return r


# ---- result 回传字段的传输编码 ----
# 上行只有 220 KB/s（实测），而两字段都是 JSON 文本 / torch 张量，gzip level 6 实测省
# 29.6%（weights.json 1.35×、opt tar 1.45×，CPU 仅 ~0.05 s）。level 9 换不到额外收益。
_GZIP_MAGIC = b"\x1f\x8b"
_WIRE_GZIP_LEVEL = 6


# ---- 会退火到 ~0 的系数：低于此阈值即视为"关" ----
# 依据（2026-09-10 实测）：kickstart / kl 系数按 `kickstart_kl * decay ** N` 几何衰减，
# **永远到不了精确 0** —— 实测课程跑到 kl = 1.4551915228366852e-11（= 2^-36）时，
# 判据 `> 0` 仍放行，于是白付：ref 权重进 payload（~0.36 MB）+ worker 每轮预计算 3 s。
# 而它的数学贡献 1.46e-11 x 0.126 ≈ 1.8e-12，相对 policy≈0.0046 完全可忽略。
# 用户已确认课程不会回抬 kickstart（2026-09-10）。
NEGLIGIBLE_COEF = 1e-9


def coef_active(x: float) -> bool:
    """系数是否值得付它的开销（> NEGLIGIBLE_COEF）。

    用于 kickstart_kl / kl_coef 这类会退火到 ~0 的旋钮；阈值以下一律按"关"处理，
    从而省掉 ref 权重传输、worker 侧 ref 加载与预计算、engine 侧 ref 前向。
    """
    return float(x) > NEGLIGIBLE_COEF


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

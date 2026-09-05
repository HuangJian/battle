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
import hashlib
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

# ------------------------------------------------------------------ constants

PROTO = 1  # 协议版本：未知字段忽略，缺失必填 fail fast（D1）
LEASE_SEC = 30 * 60  # job 租约（D1/F3）：30 分钟
HEARTBEAT_SEC = 60  # 心跳（D1/F3）：60 秒
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
    "shuffle": True,
    "schedule_raw": [],  # ppo_schedule 解析前原始表（审计）
    "opt_init": "",  # base64 tar（model/opt/numpy RNG）；空 = 无（首轮）
}


class ProtocolError(ValueError):
    """协议违规（缺失必填 / 类型错 / 哈希不匹配）。调用方（hub/worker）决定拒收方式。"""


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
    for k in ("commit", "code_sha256", "course", "course_fp", "mode", "seed", "init_weights_fp",
              "data_fp", "payload_sha256", "job_id"):
        if not isinstance(out[k], str) or not out[k]:
            raise ProtocolError(f"{k} 必须是非空 str")
    if out["mode"] != "per-tick":
        raise ProtocolError(f"mode={out['mode']!r} != 'per-tick'（v1 红线：仅 per-tick 课程支持远程）")
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

def pack_payload(shard_dirs: list[str | Path], manifest: dict, out_zip: str | Path) -> str:
    """把 shard 目录（npy + manifest.json）打成 zip，写 `out_zip`。

    zip 内布局：每个 shard 目录整体进入（目录名 rl_s{stage}_seed{seed}/…），
    根下再写一份 manifest.json（payload_sha256 占位空串——最终哈希由调用方对
    **本函数产出的 zip 字节**计算后回填 job 记录，worker 以 job 记录的
    payload_sha256 对原始下载字节校验，D1——防隧道截断）。

    返回 zip 文件字节 sha256。调用方拿到后应把 sha 写入 job 记录/账本。
    """
    zpath = Path(out_zip)
    zpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = zpath.with_suffix(zpath.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for d in shard_dirs:
            p = Path(d)
            if not p.is_dir():
                raise ProtocolError(f"pack_payload: shard 目录不存在 {p}")
            for f in sorted(p.iterdir()):
                if f.is_file():
                    z.write(f, arcname=f"{p.name}/{f.name}")
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    tmp.replace(zpath)
    return hashlib.sha256(zpath.read_bytes()).hexdigest()


def unpack_payload(zip_path: str | Path, dest: str | Path) -> tuple[dict, list[str]]:
    """解包 payload zip → (manifest, shard_dir_paths)。

    shard_dir_paths 为解包后落在 dest 下的各 shard 目录（含 manifest.json），
    供 worker 的 load_episodes 消费。返回的 manifest 为 zip 内副本（payload_sha256
    为占位空串）——**不作权威校验**；worker 必须用 job 记录（/jobs/next 返回）
    的 manifest 做 payload_sha256 / commit / mode 等全部校验（本函数只解包）。
    """
    zpath = Path(zip_path)
    dest_p = Path(dest)
    dest_p.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest_p)
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


def encode_weights_json(wj: bytes) -> str:
    """weights_json 传输编码：原始字节 → base64（JSON 安全的回传字段）。"""
    return base64.b64encode(wj).decode("ascii")


def decode_weights_json(b64: str) -> bytes:
    """weights_json 传输解码：base64 → 原始字节（hub 落盘 args.out 前用）。"""
    return base64.b64decode(b64.encode("ascii"))


def encode_opt_tar(tar_bytes: bytes) -> str:
    return base64.b64encode(tar_bytes).decode("ascii")


def decode_opt_tar(b64: str) -> bytes:
    return base64.b64decode(b64.encode("ascii"))

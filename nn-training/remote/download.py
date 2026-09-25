"""remote/download.py — worker 的**下载簇**（S4 第七刀，2026-09-23）。

从 `remote/worker.py` 搬出来的「把 hub 上的物料取到本地」：`_progress_logger`（进度行工厂）·
`download_payload` / `download_code` / `download_ts_code` / `download_blob`（四类 GET，
缺省 P1、带空闲超时与进度行）· `_cache_blob` / `_resolve_blob`（内容寻址 blob 缓存 +
安全阀：manifest 带 sha 且取不到就**响亮失败**，绝不静默退回 warm-start）。

## 「物料落地三兄弟」（S4 第十二刀，2026-09-24）

取字节与**把它摆好**是同一件事的两半，三个兄弟现在住一起（`_ensure_*`，各返回一个
`NamedTuple` 说明「摆到哪儿了」）：

| 兄弟 | 物料 | 落地到 |
|---|---|---|
| `_ensure_payload` | payload.tar.xz | 清场重建 `work_dir/<jid>/` + 解出 shard 目录 |
| `_ensure_code` | code.zip | `code_cache/<sha>/` + `sys.path[0]`（内容寻址，跨课复用） |
| `_ensure_ts_code` | TS 运行时 zip | `ts_code_cache/<sha>/`（bun 的 cwd，**另一棵树**） |

三者同规：按 sha 内容寻址 + tmp 原子改名 + sha 不匹配 ⇒ `RetryableError`（传输损坏，
重下可修复，不是内容决定性失败）。放在一起的理由不是「都跟下载有关」，而是它们的
**判据同源**——谁改其中一条的失败语义，另两条必然要跟着改。

## 依赖方向

`download → {http, wire, bulk_sched, job_fs}`（全向下，DAG）：下载走 `remote.http._get_with_retry`
（退避重试 + 低速重抽 + 传输账），零字节命中记 `remote.wire._wire_hit`，P1 优先级取
`remote.bulk_sched.BULK_P1_CRITICAL`，超时阈值取 `remote.http.BODY_*`（单一定义）；
作业物料 I/O（`JOB_DIR_KEEP` / `prune_job_dirs` / `unpack_payload_or_fail`）取 `remote.job_fs`。
**不** import `remote.worker`（`worker` 用自别名转发回来）。

## 注入点（S4 第十二刀后重划）

`download_*` 的调用点分三档，**patch 目标随实现走**：

1. **组内**（`_resolve_blob` → `download_blob`、`_ensure_ts_code` → `download_ts_code`、
   `_ensure_payload` → `download_payload`、`_ensure_code` → `download_code`）⇒ 全部解析在
   **本模块**，测试必须 patch `remote.download.*`。第十二刀把物料落地整段搬进本模块之后，
   **`payload` / `code` 两个下载函数**的调用点也归到了这一档。
2. **`job_round._prefetch_fill`**（第十刀搬走的预取填充器）⇒ patch `remote.job_round.*`。
3. **`remote.worker`**：宿主 `run_job` 现在自己读的只有**物料落地三兄弟**
   （`_ensure_payload` / `_ensure_code` / `_ensure_ts_code`）——`download_*` / `_cache_blob` /
   `_resolve_blob` / `_progress_logger` 在 `worker` 命名空间**已无读者**，那些转发仍留着是因为
   tests 把 `remote.worker` 当**取名字的入口**直接调（名字是契约，位置不是）；但它们**不再是注入点**
   ——`monkeypatch.setattr(worker, "download_payload", …)` 会静默失效（这正是
   `tests/test_remote_ppo.py` 的缓存命中用例要交给本模块的原因）。
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

from common.protocol import (
    BLOB_INIT,
    BLOB_OPT,
    BLOB_REF,
    PAYLOAD_NAME,
    ProtocolError,
    RetryableError,
    decode_opt_tar,
    is_content_sha,
)
from remote.bulk_sched import BULK_P1_CRITICAL
from remote.http import (
    BODY_IDLE_TIMEOUT_SEC,
    BODY_TOTAL_TIMEOUT_SEC,
    _get_with_retry,
)
from remote.job_fs import JOB_DIR_KEEP, prune_job_dirs, unpack_payload_or_fail
from remote.wire import _wire_hit

__all__ = [
    "WEIGHT_SOURCES",
    "CodeLanded",
    "PayloadLanded",
    "_cache_blob",
    "_cache_produced_weights",
    "_ensure_code",
    "_ensure_payload",
    "_ensure_ts_code",
    "_progress_logger",
    "_resolve_blob",
    "_resolve_weights",
    "download_blob",
    "download_code",
    "download_payload",
    "download_ts_code",
]


def _progress_logger(label: str, log: Any):
    """进度行工厂：`job X: payload 下载中 3.20 MB / 4.85 MB (66%) 用时 12s（270 KB/s）`。"""

    def _report(got: int, total: int, elapsed: float) -> None:
        mb = 1024.0 * 1024.0
        rate = (got / elapsed / 1024.0) if elapsed > 0 else 0.0
        pct = f" ({got * 100 // total}%)" if total > 0 else ""
        of = f" / {total / mb:.2f} MB" if total > 0 else ""
        log(f"{label} 下载中 {got / mb:.2f} MB{of}{pct} 用时 {elapsed:.0f}s（{rate:.0f} KB/s）")

    return _report


def download_payload(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    bulk_prio: str = BULK_P1_CRITICAL,
    wire_jid: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """取 payload 归档：**分块 + 进度行 + 停滞即断**（2026-09-20 事故的修复面）。

    `wire_jid`：传输账挂给哪个 job（缺省 = 本 job）。预取路径传合成 id，免得一份提前下载
    的账记进「正在跑的那个 job」里——那会让阶段占比把预取时间算成别人的 T_in。

    停滞判据 = 45s 无新字节（`BODY_IDLE_TIMEOUT_SEC`），总预算 300s。原来只有
    一个 `timeout=300` 的整读：隧道/代理中途停滞时，操作员看到的是**几分钟零输出**
    且日志里连一句「失败」都没有（socket 超时的裸异常没有正文）。

    `bulk_prio`（2026-09-22）：开算前的关键下载用缺省 P1；**预取**（软持有）传
    `BULK_P2_PREFETCH`——它必须能在高优传输到达时丢掉半截（`BulkPreemptError`）。
    """
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/payload",
        timeout=BODY_TOTAL_TIMEOUT_SEC,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=BODY_TOTAL_TIMEOUT_SEC,
        progress=_progress_logger(f"job {jid}: payload", log),
        wire_jid=wire_jid or jid,
        wire_seg="payload",
        reroll=True,
        bulk_prio=bulk_prio,
    )


def download_code(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    # code.zip 同样走隧道（1-3MB）：与 payload 同规的停滞判据与进度行。
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/code",
        timeout=120.0,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=120.0,
        progress=_progress_logger(f"job {jid}: code", log),
        wire_jid=jid,
        wire_seg="code",
        reroll=True,
    )


def download_ts_code(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """M3：取 TS 运行时 zip（kind=iter 的节点要用 bun 跑 rollout）。

    代价只付一次：内容寻址缓存（`ts_code_cache/<sha>`）命中后同 sha 永不重下。
    """
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/ts_code",
        timeout=BODY_TOTAL_TIMEOUT_SEC,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=BODY_TOTAL_TIMEOUT_SEC,
        progress=_progress_logger(f"job {jid}: ts_code", log),
        wire_jid=jid,
        wire_seg="ts_code",
        reroll=True,
    )


def download_blob(
    base_url: str,
    token: str,
    jid: str,
    name: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """M2 B3：取内容寻址 blob（raw opt/ref）。404 = 确定性缺失（响亮失败，非静默降级）。"""
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/blob?name={name}",
        timeout=BODY_TOTAL_TIMEOUT_SEC,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=BODY_TOTAL_TIMEOUT_SEC,
        progress=_progress_logger(f"job {jid}: blob {name}", log),
        wire_jid=jid,
        wire_seg=f"blob:{name}",
        reroll=True,
    )


def _cache_produced_weights(blob_root: Path, raw: bytes, log) -> str:
    """把**本轮产出的** `weights.json` 原始字节写进 `blob_cache/<sha256(raw)>`；返回该 sha。

    opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md §3.2 W1 / §3.6-8）：下一轮 hub 的
    `init_weights_fp` = `sha256(args.out)`，而 `args.out` 就是这份字节（`verify_and_land`
    落的就是 `result.weights_json` 解码后的原字节）⇒ 同会话内 `init` blob 100% 命中、
    下行零字节。

    **少了这一步**：`init` 的键每轮都变、每轮必 miss ⇒ 上行省 268,996 B 而下行多付
    379,114 B = **净亏 110 KB/轮**（评审 F1）。与 opt tar 在 `run_job` 里的缓存写入
    （`_cache_blob(blob_root, sha256(opt_tar_raw), …)`）是同一手法、同一个理由。
    """
    sha = hashlib.sha256(raw).hexdigest()
    _cache_blob(blob_root, sha, raw, log)
    return sha


def _cache_blob(blob_root: Path, sha: str, raw: bytes, log) -> None:
    """把 raw blob 写入 `blob_cache/<sha>`（原子改名；写失败只记日志）。"""
    if not sha or not raw:
        return
    try:
        blob_root.mkdir(parents=True, exist_ok=True)
        p = blob_root / sha
        if p.exists():
            return
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(p)
    except OSError as e:
        log(f"blob cache 写失败（{e}）——下一轮将重传该 blob")


def _resolve_blob(
    *,
    blob_root: Path,
    name: str,
    sha: str,
    inline_b64: str,
    jid: str,
    base_url: str,
    token: str,
    preloaded: dict | None,
    log,
) -> tuple[bytes, bool, str]:
    """解析一个 M2 blob → (raw, hit, src)；src ∈ cache|preloaded|download|inline|none。

    安全阀（plan §4.3）：`sha` 非空时**只认内容寻址路径** —— 缓存命中 / preloaded /
    HTTP 取，任一校验不过就响亮失败（RetryableError/ProtocolError）；**绝不**退回
    内联或 warm-start（那会静默把 D5 的 Adam 动量丢掉）。`sha` 为空 = 未开瘦身/
    旧 hub，走内联（内联也空则返回 none，由调用方决定策略）。
    """
    import hashlib as _hl

    if sha:
        cp = blob_root / sha
        if cp.exists():
            raw = cp.read_bytes()
            if _hl.sha256(raw).hexdigest() == sha:
                _wire_hit(jid, f"blob:{name}")
                return raw, True, "cache"
            log(f"blob {name}: cache 命中但 sha 不符（损坏）——重新取")
        pl = (preloaded or {}).get("blobs") or {}
        if name in pl:
            raw = pl[name]
            src = "preloaded"
            _wire_hit(jid, f"blob:{name}", "preloaded")
        else:
            raw = download_blob(base_url, token, jid, name, log=log)
            src = "download"
        if _hl.sha256(raw).hexdigest() != sha:
            # 传输损坏 = 瞬时（同 payload_sha256 口径）：重下可修复
            raise RetryableError(f"blob {name} sha 不匹配——传输损坏（重下可修复）")
        _cache_blob(blob_root, sha, raw, log)
        return raw, False, src
    if name == BLOB_OPT and inline_b64:
        return decode_opt_tar(inline_b64), True, "inline"
    if name == BLOB_REF and inline_b64:
        import base64 as _b64

        return _b64.b64decode(inline_b64.encode("ascii")), True, "inline"
    return b"", False, "none"


#: `init_weights_fp` 的**哨兵**值（BC job 与全新 run 首轮都没有内容寻址的权重）：
#: 见到它们 ⇒ 零网络请求，payload 有就照用、没有就 none（§3.2 的 64-hex 规则）。
_SENTINEL_INIT_FP: tuple[str, ...] = ("", "bc")

#: 初始权重的**五源**（优先级即顺序；W4 的 `legacy_tar` 由调用方的 restore 段判定）。
WEIGHT_SOURCES: tuple[str, ...] = ("payload", "cache", "preloaded", "download", "legacy_tar")


def _resolve_weights(
    *,
    job_dir: Path,
    init_weights_fp: str,
    blob_root: Path,
    jid: str,
    base_url: str,
    token: str,
    preloaded: dict | None,
    log,
) -> tuple[Path | None, str, int]:
    """解析本轮的初始权重 → `(path | None, src, wire_bytes)`。

    `src ∈ payload|cache|preloaded|download|none`（`legacy_tar` 由调用方在 restore 段判定）；
    `wire_bytes` = **走网络的字节数**（命中/payload 恒 0，`download` 才是 raw 长度）——
    它是「这一刀省没省下来」的直接读数（§4 的 `wire.weights_bytes`）。

    plan/opt-blob-diet.plan.md §3.2 的五源优先级（先到先用）：

      W0 `job_dir/init_weights.json`（payload 随包带走：离线腿/bundle/冒烟/非 slim/旧 hub）
      W1 `blob_cache/<init_weights_fp>`（稳态命中，零字节 —— **靠调用方产物段缓存
         自己产出的权重**：hub 下一轮的 `init_weights_fp` = `sha256(args.out)` = 同一份字节）
      W2 `preloaded["blobs"]["init"]`（push 腿）
      W3 `GET /jobs/{id}/blob?name=init`（换机首次；sha 必校）

    **失败分类**（§3.2，与 `_resolve_blob` 的安全阀同口径）：W1–W3 的**瞬时**失败
    （5xx / 网络 / 下回来的字节 sha 不符）在函数内就抛 `RetryableError` —— 那是「重领重下
    能修」的，绝不能报成确定性失败（`report_job_failure` 会停掉一条腿）。W3 的**确定性**
    不可得（404 / 旧 hub 的 400 未知名）只记一行并返回 `none`：由调用方决定走 W4 还是
    响亮拒绝。**绝不静默 warm-start**（新开 Adam + 随机权重地把一轮跑成看起来正常）。

    `init_weights_fp` 是哨兵（`""`/`"bc"`，即 BC job 或全新 run 首轮）：**零网络请求** ——
    payload 有就照用，没有就返回 `none`（§3.2 的 64-hex 规则）。
    """
    path = job_dir / "init_weights.json"
    fp = str(init_weights_fp or "")
    if fp in _SENTINEL_INIT_FP or not is_content_sha(fp):
        if path.exists():
            return path, "payload", 0
        return None, "none", 0
    # ---- W0：payload 内那份（随包可离线跑）—— **照样校 sha**（§3.2 / 评审 F4）----
    if path.exists():
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() == fp:
            return path, "payload", 0
        # 坏字节：作废这份 + 记一行 + 回源（绝不用坏字节，也绝不静默）
        log(f"job {jid}: payload 内 init_weights.json 与 init_weights_fp 不符 —— 作废，改走 blob")
        try:
            path.unlink()
        except OSError:
            pass
    # ---- W1/W2/W3：一律走 `_resolve_blob`（内容寻址 + sha 校验 + 命中即写 cache）----
    try:
        raw, hit, src = _resolve_blob(
            blob_root=blob_root,
            name=BLOB_INIT,
            sha=fp,
            inline_b64="",
            jid=jid,
            base_url=base_url,
            token=token,
            preloaded=preloaded,
            log=log,
        )
    except RetryableError:
        raise  # 瞬时：重领重下能修（§3.2 的失败分类红线）
    except ProtocolError as e:
        # 确定性不可得（404 / 未知名 400）—— **不**在这里定生死：W4 可能救它（§3.5 第三行）
        log(f"job {jid}: init blob 不可得（{e}）—— 看 tar 里有没有旧形状的 model.pt")
        return None, "none", 0
    if not raw:
        return None, "none", 0
    path.write_bytes(raw)
    resolved = "cache" if (hit and src == "cache") else src
    if resolved != "cache":
        log(f"job {jid}: init 权重来源 src={resolved}（{len(raw)} bytes，fp={fp[:12]}…）")
    return path, resolved, (len(raw) if resolved == "download" else 0)


class PayloadLanded(NamedTuple):
    """payload 落地的结果（物料三兄弟之一，另两个见 `_ensure_code` / `_ensure_ts_code`）。

    * `payload_bytes` —— 原始字节（调用方要拿 `len()` 记传输账，不重读文件）。
    * `job_dir` —— 本 job 的工作目录（**已清场重建**：上一轮的 shard / 产物一律不留）。
    * `shard_dirs` —— 解包出的 shard 目录（D14 血缘校验与训练链都要）。
    * `dl_sec` —— 取到字节的墙钟（push 携带时为 `0.0`：字节没走网络，这段账归 hub）。
    * `unpack_sec` —— 解包墙钟（M0 统一计量的另一段）。
    """

    payload_bytes: bytes
    job_dir: Path
    shard_dirs: list[Any]
    dl_sec: float
    unpack_sec: float


class CodeLanded(NamedTuple):
    """code.zip 落地后的三处目录——**三处都要回传**，调用方不许自己再推导一遍。

    * `code_root` —— 内容寻址缓存的**根**（跨课共享；`ts_code_cache` 与它同级，
      `blob_cache` 是它的父目录下的一员）。
    * `code_cache_dir` —— 本 job 代码的 per-sha 目录，**已经插进 `sys.path[0]`**。
    * `blob_root` —— M2 的 blob 缓存根（键 = raw sha256）。

    为什么返回目录而不是只返回 `code_cache_dir`：调用方还要用 `code_root.parent` 算
    `ts_code_cache`、用 `blob_root` 喂训练核。「根目录推导」写两遍就是两个口径。
    """

    code_root: Path
    code_cache_dir: Path
    blob_root: Path


def _ensure_payload(
    base_url: str,
    token: str,
    jid: str,
    manifest: dict,
    work_dir: Path,
    *,
    preloaded: dict | None = None,
    log=lambda msg: None,
    bundle: Any = None,
) -> PayloadLanded:
    """把 payload 取到本地并摆好：下载（或 push 携带）→ sha 校验 → 清场 → 解包。

    ## 为什么「校验」在这里而不是调用方

    `payload_sha256` 不匹配属**瞬时故障**（传输截断/损坏）⇒ `RetryableError`，
    调用方会释放租约、立即重领重下。这条语义与 code / ts_code 同规，写在同一个模块里
    才不会三条线各漂一遍（内容决定性失败走 `ProtocolError`，那是另一码事——见解包那行）。

    ## 为什么清场在解包**之前**、prune 在清场**之后**

    清场重建保证解包面对的是一个空目录（上一轮的 shard/产物残留会让 D14 与训练链读到
    脏数据）；prune 放在开头是为了「失败轮也照样清理」——放到末尾的话，本轮炸了就永远
    轮转不掉旧目录。两条顺序都是踩出来的，别顺手调换。

    参数走显式传递（`base_url` / `token` / `jid` / `manifest` / `work_dir` + `preloaded`）：
    本模块**不认识** worker 的作业状态，也不知道有几个 hub（那都是宿主的事）。
    """
    # M0 统一计量：payload 大小与拿到它的墙钟（push = 随 POST body 抵达，下载耗时归 hub；
    # pull = 真下载时间）。其余拆分（unpack/opt_restore/grad）各自包在下面。
    t_dl = time.time()
    if preloaded is not None and "payload_zip" in preloaded:
        raw = preloaded["payload_zip"]
        _line = f"push {len(raw)} bytes"
        payload_dl_sec = 0.0
    else:
        raw = download_payload(base_url, token, jid)
        payload_dl_sec = round(time.time() - t_dl, 3)
        _line = f"下载 {len(raw)} bytes / {payload_dl_sec:.1f}s"
    # 日志节食（2026-09-24）：给了 bundle 就攒进调用方那一行（短口径，见 log_bundle）；
    # 不传时逐字节保持改造前的输出。
    if bundle is not None:
        bundle.add("payload", _line)
    elif preloaded is not None and "payload_zip" in preloaded:
        log(f"job {jid}: payload from push ({len(raw)} bytes)")
    else:
        log(f"job {jid}: payload downloaded ({len(raw)} bytes in {payload_dl_sec:.1f}s)")
    if hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]:
        # 传输损坏属瞬时故障：重下即可修复（RetryableError → 释放租约立即重领重下）
        raise RetryableError("payload_sha256 不匹配——传输损坏（重下可修复）")

    job_dir = work_dir / jid
    if job_dir.exists():
        from platform_utils import rmtree_best_effort

        rmtree_best_effort(job_dir)
    job_dir.mkdir(parents=True)
    # 磁盘：本 job 之后最多留 JOB_DIR_KEEP 个目录（放开头 = 失败轮也照样清理）
    prune_job_dirs(work_dir, JOB_DIR_KEEP, log=log, bundle=bundle)
    zip_path = job_dir / PAYLOAD_NAME
    zip_path.write_bytes(raw)
    # zip 内 manifest 是占位副本，解包仅取 shard 目录；权威校验全走 job 记录 manifest。
    # init_weights.json / opt_init.tar.b64 与 shard 目录同落 job_dir 根（解包天然如此）。
    t_unpack = time.time()
    # 解包失败 = 内容决定性失败（走 ProtocolError → 确定性上报，不重认领）——见 §4 事故。
    _unused_manifest, shard_dirs = unpack_payload_or_fail(zip_path, job_dir)
    unpack_sec = round(time.time() - t_unpack, 3)
    return PayloadLanded(
        payload_bytes=raw,
        job_dir=job_dir,
        shard_dirs=shard_dirs,
        dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
    )


def _ensure_code(
    base_url: str,
    token: str,
    jid: str,
    manifest: dict,
    job_dir: Path,
    work_dir: Path,
    *,
    code_cache_root: Path | None = None,
    preloaded: dict | None = None,
    log=lambda msg: None,
    bundle: Any = None,
) -> CodeLanded:
    """把 code.zip 摆到本地并**让它可 import**：内容寻址缓存 → 解包 → `sys.path[0]`。

    替代 git 同步（D6）：云端 worker 不再 `git checkout`，而是用 hub 启动时打包的代码快照。

    ## 内容寻址缓存（2026-09-05）

    code 在多次迭代间通常不变（sha 只由源码内容决定，pack 侧时间戳已固定化），缓存命中即省
    一次隧道下载 + 解压。缓存目录按 sha 隔离，tmp 原子改名防半截。

    ## `code_cache_root`（而不是 `code_cache_dir`）

    参数是**根**：多课程共享 worker（P3b C3）下 code_cache 留共享根（按 sha 内容寻址，跨课
    复用），只有 job 目录按源分区。名字刻意区分「根」与「per-sha 子目录」——改造前这两者
    在宿主里是同一个变量名，读的人要靠上下文猜。

    ## 为什么 `sys.path.insert` 在这里

    落地与「可 import」是这一步的同一件事：`sys.path` 只影响**尚未导入**的模块，所以插完
    还要宿主那道热替换护栏（`_ACTIVE_CODE_SHA`）去挡「已 import 的旧代码」——那是**进程**
    的状态，因此留宿主，不搬。
    """
    # ---- commit 校验：下载 code.zip 解压到 sys.path（替代 git 同步，D6） ----
    # 云端 worker 不再依赖 git checkout，而是使用 hub 启动时打包的代码快照。
    # ---- code.zip 内容寻址缓存（2026-09-05）：同 sha 只下载/解压一次 ----
    # code 在多次迭代间通常不变（sha 只由源码内容决定，pack 侧时间戳已固定化），
    # 缓存命中即省一次隧道下载 + 解压。缓存目录按 sha 隔离，tmp 原子改名防半截。
    # 多课程共享 worker（P3b C3）：code_cache 留共享根（按 sha 内容寻址，跨课复用），
    # 只有 job 目录按源分区——调用方经 code_cache_root 传入共享根。
    code_root = code_cache_root if code_cache_root is not None else work_dir / "code_cache"
    #: M2 B3 blob 缓存根（跨课共享，同 code_cache 约定；键 = raw sha256）。
    blob_root = code_root.parent / "blob_cache"
    cache_dir = code_root / manifest["code_sha256"]
    if cache_dir.exists():
        sys.path.insert(0, str(cache_dir))
        _wire_hit(jid, "code")  # 零字节命中也要进本 job 的传输账（否则摘要读数失真）
        if bundle is not None:
            bundle.add("code", f"cache 命中（{manifest['code_sha256'][:12]}…）——跳过下载解压")
        else:
            log(f"job {jid}: code cache 命中（{manifest['code_sha256'][:12]}…）——跳过下载解压")
    else:
        if preloaded is not None and "code_zip" in preloaded:
            code_raw = preloaded["code_zip"]
        else:
            code_raw = download_code(base_url, token, jid)
        if hashlib.sha256(code_raw).hexdigest() != manifest["code_sha256"]:
            # per-job 快照 sha 与 manifest 对账：不匹配 = 传输损坏（瞬时，重下可修复）
            raise RetryableError("code_sha256 不匹配——传输损坏（重下可修复）")
        import zipfile

        code_extract_tmp = code_root / (manifest["code_sha256"] + ".tmp")
        code_extract_tmp.mkdir(parents=True, exist_ok=True)
        code_zip_path = job_dir / "code.zip"
        code_zip_path.write_bytes(code_raw)
        with zipfile.ZipFile(code_zip_path) as zf:
            zf.extractall(code_extract_tmp)
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        code_extract_tmp.rename(cache_dir)
        sys.path.insert(0, str(cache_dir))
        _line = (
            f"code.zip unpacked ({len(code_raw)} bytes, "
            f"{len(list(cache_dir.rglob('*.py')))} .py files) -> sys.path[0]"
        )
        if bundle is not None:
            bundle.add("code", _line)
        else:
            log(f"job {jid}: {_line}")
    return CodeLanded(code_root=code_root, code_cache_dir=cache_dir, blob_root=blob_root)


def _ensure_ts_code(
    base_url: str,
    token: str,
    jid: str,
    manifest: dict,
    *,
    ts_root: Path,
    preloaded: dict | None,
    log=lambda msg: None,
    bundle: Any = None,
) -> tuple[Path, int, bool]:
    """M3 kind=iter：把 TS 运行时 zip 解包到内容寻址目录，返回 `(目录, 字节数, 缓存命中)`。

    与 code.zip 同口径（按 sha 隔离 + tmp 原子改名），但**另一棵缓存树**：Python 代码走
    `sys.path`，TS 代码走 bun 的 cwd —— 两者生命周期/内容无关，混在一起只会让删除豁免
    名单变难维护。sha 不匹配 = 传输损坏（瞬时）⇒ RetryableError，与 payload/code 同规。

    返回的字节数用于 M0 计量（`wire.ts_code_bytes`；缓存命中时为 0 = 本轮没走这条线）。
    """
    import zipfile

    sha = str(manifest.get("ts_code_sha256", "") or "")
    if not sha:
        raise ProtocolError("kind=iter 的 manifest 缺 ts_code_sha256——无法定位 TS 运行时")
    cache = ts_root / sha
    if cache.exists():
        _wire_hit(jid, "ts_code")  # 零字节命中也要进账（与 code 同规，否则 wire 摘要读数失真）
        if bundle is not None:
            bundle.add("ts_code", f"cache 命中（{sha[:12]}…）——跳过下载解压")
        else:
            log(f"job {jid}: ts_code cache 命中（{sha[:12]}…）——跳过下载解压")
        return cache, 0, True
    raw = (preloaded or {}).get("ts_code_zip") or download_ts_code(base_url, token, jid, log=log)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise RetryableError("ts_code_sha256 不匹配——传输损坏（重下可修复）")
    tmp = ts_root / (sha + ".tmp")
    if tmp.exists():
        from platform_utils import rmtree_best_effort

        rmtree_best_effort(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp.parent / (sha + ".zip")
    zip_path.write_bytes(raw)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    ts_root.mkdir(parents=True, exist_ok=True)
    if cache.exists():  # 并发窗口：别人已解好 → 用别人的
        from platform_utils import rmtree_best_effort as _rm

        _rm(tmp, ignore_errors=True)
        _rm(zip_path, ignore_errors=True)
        return cache, 0, True
    tmp.rename(cache)
    try:
        zip_path.unlink()
    except OSError:
        pass
    n_ts = len(list(cache.rglob("*.ts")))
    _line = f"ts_code.zip unpacked ({len(raw)} bytes, {n_ts} .ts files) -> {cache.name}"
    # 日志节食（2026-09-24）：给了 bundle 就攒进调用方那一行（与 code/payload/设备同属
    # 「本 job 准备」阶段），否则逐字节保持原输出。
    if bundle is not None:
        bundle.add("ts_code", _line)
    else:
        log(f"job {jid}: {_line}")
    return cache, len(raw), False

"""remote/download.py — worker 的**下载簇**（S4 第七刀，2026-09-23）。

从 `remote/worker.py` 搬出来的「把 hub 上的物料取到本地」：`_progress_logger`（进度行工厂）·
`download_payload` / `download_code` / `download_ts_code` / `download_blob`（四类 GET，
缺省 P1、带空闲超时与进度行）· `_cache_blob` / `_resolve_blob`（内容寻址 blob 缓存 +
安全阀：manifest 带 sha 且取不到就**响亮失败**，绝不静默退回 warm-start）· `_ensure_ts_code`
（kind=iter 的 TS 代码物料化：缓存命中 / 解包 / sha 校验）。

## 依赖方向

`download → {http, wire, bulk_sched}`（全向下，DAG）：下载走 `remote.http._get_with_retry`
（退避重试 + 低速重抽 + 传输账），零字节命中记 `remote.wire._wire_hit`，P1 优先级取
`remote.bulk_sched.BULK_P1_CRITICAL`，超时阈值取 `remote.http.BODY_*`（单一定义）。
**不** import `remote.worker`（`worker` 用自别名转发回来）。

## 注入点

本组的**调用方全部是宿主**（`run_job` / `_prefetch_fill`）⇒ patch `remote.worker.download_*`
仍然有效（宿主在 `worker` 命名空间解析）。唯一例外是**组内互调**：`_resolve_blob` 调
`download_blob`、`_ensure_ts_code` 调 `download_ts_code`——它们在本模块命名空间解析，
所以「patch 后调 `_resolve_blob` / `_ensure_ts_code`」的测试必须 patch **本模块**
（`tests/test_remote_ppo.py` 的两处 `download_blob` 即此情形）。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from common.protocol import (
    BLOB_OPT,
    BLOB_REF,
    ProtocolError,
    RetryableError,
    decode_opt_tar,
)
from remote.bulk_sched import BULK_P1_CRITICAL
from remote.http import (
    BODY_IDLE_TIMEOUT_SEC,
    BODY_TOTAL_TIMEOUT_SEC,
    _get_with_retry,
)
from remote.wire import _wire_hit

__all__ = [
    "_cache_blob",
    "_ensure_ts_code",
    "_progress_logger",
    "_resolve_blob",
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


def _ensure_ts_code(
    base_url: str,
    token: str,
    jid: str,
    manifest: dict,
    *,
    ts_root: Path,
    preloaded: dict | None,
    log=lambda msg: None,
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
    log(
        f"job {jid}: ts_code.zip unpacked ({len(raw)} bytes, {n_ts} .ts files) -> {cache.name}"
    )
    return cache, len(raw), False
